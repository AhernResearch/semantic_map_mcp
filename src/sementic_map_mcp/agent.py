"""Bounded, read-only visual grounding loop using a local chat-completions endpoint."""
import base64
import json
import os
from time import perf_counter
from pathlib import Path
from urllib.request import Request, urlopen

from jsonschema import validate

from .store import SCHEMAS, read_json, require, write_json

PROMPT = '''你是语义地图助手，负责根据用户描述找到目标物体。
优先直接 search_candidates 获取候选及物体图片，根据图片判断。
图片足够清楚时直接提交；只有细节不足或需要第二视角时调用 inspect_objects。
只有区域或类别需要核对时才调用 list_scene；独立查询尽量合并，同轮可批量调用独立工具。
先看最相关候选的图片，足以判断后直接提交；仅在具体歧义需要时翻页、换视角或补充空间图。
已获取的相同图片和相同查询结果直接复用，不重复调用。
所有工具的queries必须使用英文；先把用户中文描述转换为简洁、具体的英文检索短语，不提交中文或中英混合词。
首轮优先使用一个明确的英文描述，不批量堆砌同义词；需要空间布局时调用 view_map。
若当前候选已有明确匹配，立即finish，不为确认已有结论继续检索。
若多个具体候选符合条件且区别取决于用户未提供的信息，立即提出具体澄清问题，无需机械翻完全部页。
每次改写queries或room_ids必须从page=1开始；只有queries与room_ids完全不变时才能沿next_page翻页。
无匹配时，先判断是需要翻next_page还是改变检索概念；每次继续检索都应针对具体缺口，避免换同义词重复查看相同对象。
根据物体图片判断颜色、形状、摆放和可见状态，并结合用户条件选择目标。
本任务以地图图片呈现的场景为查询范围，用户描述的占用等状态条件按图片中的可见状态匹配。
有明确匹配就选择；只有影响选择的具体歧义才追问。说明简短、具体。
调用 finish 提交结果：status、object_ids、evidence_ids、reason、question。
选定一个目标用 resolved，evidence_ids 引用对应物体图片的 media_id；无需追问时 question=null。
多个目标难以区分用 ambiguous，缺少图片或相关信息用 insufficient_evidence，缺参照上下文用 needs_context，取消用 cancelled。
'''

ENGLISH_QUERY_POLICY = ('queries必须全部使用英文，不得包含中文或中英混合描述。'
    '先将用户描述转换为具体英文短语，首轮优先1条，不堆砌同义词；工具不会自动翻译。')

# Keep acquisition/provenance metadata in raw traces, outside the model's task context.
AUDIT_FIELDS = {'source_kind', 'source_refs', 'source_links', 'provenance', 'identity_status',
                'limitations', 'uri'}
MODEL_PAGE_SIZE = 5
MODEL_MAX_INSPECT_IDS = 3


def clean_model_context(item):
    if isinstance(item, dict):
        return {k: clean_model_context(v) for k, v in item.items() if k not in AUDIT_FIELDS}
    if isinstance(item, list):
        return [clean_model_context(v) for v in item]
    return item


def model_context(value, arguments=None):
    result = clean_model_context(value)
    args = arguments or {}
    data = result.get('data')
    if not data:
        return result
    if result['tool'] == 'list_scene':
        data['semantic_type_count'] = len(data['semantic_types'])
        if not args.get('include_types', False):
            del data['semantic_types']
            data['rooms'] = [{k: v for k, v in room.items() if k != 'type_ids'} for room in data['rooms']]
        data['capabilities'] = {name: cap['status'] for name, cap in data['capabilities'].items()
                                if name != ('visual_search' if data['retrieval']['mode'] != 'visual' else 'multimodal_search')}
        policy = data['query_policy']
        data['query_policy'] = dict(vector_top_k=policy['visual_top_k'], page_size=MODEL_PAGE_SIZE,
            max_queries=policy['max_queries'], max_inspect_ids=MODEL_MAX_INSPECT_IDS, views_per_object=1)
    elif result['tool'] == 'search_candidates':
        candidates = sorted(data['candidates'], key=lambda c: (
            -max((s['fusion_score'] or 0 for s in c['scores']), default=0), c['object_id']))
        start = (args.get('page', 1) - 1) * MODEL_PAGE_SIZE
        page = candidates[start:start + MODEL_PAGE_SIZE]
        result['data'] = dict(candidates=[dict(object_id=c['object_id'], label=c['label'],
            room_ids=c['room_ids'], matched_queries=c['matched_queries']) for c in page],
            total_candidates=len(candidates), next_page=args.get('page', 1) + 1 if start + MODEL_PAGE_SIZE < len(candidates) else None,
            retrieval_truncated=any(g['truncated'] for g in data['groups']))
    elif result['tool'] == 'inspect_objects':
        view = args.get('view', 1) - 1
        objects = []
        for o in data['objects']:
            evidence = o['evidence'][view:view + 1]
            objects.append(dict(object_id=o['object_id'], label=o['label'], room_ids=o['room_ids'],
                center_xyz=o['center_xyz'], attributes=o['attributes'], evidence=evidence,
                next_view=view + 2 if view + 1 < len(o['evidence']) else None))
        result['data'] = dict(objects=objects, missing_ids=data['missing_ids'])
        ids = {e['media_id'] for o in objects for e in o['evidence']}
        result['media'] = [m for m in result['media'] if m['media_id'] in ids]
    return result


def model_tools(search_preview=True, use_descriptions=False, decision_schema=None):
    definitions = read_json(SCHEMAS / 'tools.json')['tools']
    for t in definitions:
        if t['name'] in ('search_candidates', 'view_map'):
            t['description'] += ' ' + ENGLISH_QUERY_POLICY
            t['inputSchema']['properties']['queries']['items']['description'] = ENGLISH_QUERY_POLICY
        if t['name'] == 'list_scene':
            t['inputSchema']['properties']['include_types'] = dict(type='boolean', default=False)
            t['description'] += ' 默认只返回紧凑场景概览、区域、覆盖率和工具限制；需要完整类别/英文别名时传include_types=true。已知目标描述可直接检索。'
        if t['name'] == 'view_map':
            t['description'] += ' 仅需要空间关系时使用，不是查询的必经步骤。'
        if t['name'] == 'search_candidates':
            t['inputSchema']['properties']['page'] = dict(type='integer', minimum=1)
            t['description'] += ' 改写queries或room_ids必须重置page=1；只有二者完全不变才可传next_page翻页，不要跳过新查询首屏。明确匹配就结束检索；多个具体匹配且缺少用户条件时直接澄清。无匹配时按具体缺口翻页或改写，避免重复同义检索。每页5个候选，用相同查询和page翻页；next_page为空表示本次召回池已读完。'
            if search_preview and use_descriptions:
                t['description'] += ' 返回候选物体的离线外观描述和description_id，可根据描述直接选择；需要额外细节时inspect_objects。'
            elif search_preview:
                t['description'] += ' 同时返回带ID的物体图片，可直接据图选择；细节不足再inspect_objects。'
        if t['name'] == 'inspect_objects':
            t['inputSchema']['properties']['ids']['maxItems'] = MODEL_MAX_INSPECT_IDS
            t['inputSchema']['properties']['view'] = dict(type='integer', minimum=1, maximum=2)
            t['description'] += ' 每次最多3个对象，每个默认1张图片；需要另一视角时传view=2。'
    functions = [dict(type='function', function=dict(name=t['name'],
                 description=t.get('description', ''), parameters=t['inputSchema'])) for t in definitions]
    decision_parameters = dict(decision_schema) if decision_schema is not None else read_json(SCHEMAS / 'grounding_decision.json')
    # Anthropic rejects top-level allOf; full conditional validation stays in the host.
    decision_parameters.pop('allOf', None)
    functions.append(dict(type='function', function=dict(name='finish',
        description='提交选定区域、物体或需要澄清的问题。' if 'room_ids' in decision_parameters['properties'] else '提交选定物体或需要澄清的问题。',
        parameters=decision_parameters)))
    return functions


def model_request(model, messages, tools, tool_choice=None):
    payload = dict(model=model, messages=messages, tools=tools, temperature=0, max_tokens=1600)
    if tool_choice is not None:
        payload['tool_choice'] = tool_choice
    return payload


def chat(endpoint, model, messages, search_preview=True, use_descriptions=False, tools=None, request_log=None):
    headers = {'Content-Type': 'application/json'}
    if token := os.environ.get('MAP_AGENT_API_KEY'):
        headers['Authorization'] = 'Bearer ' + token
    payload = model_request(model, messages, tools if tools is not None else model_tools(search_preview, use_descriptions))
    if request_log is not None:
        write_json(request_log, payload)
    request = Request(endpoint.rstrip('/') + '/chat/completions',
                      data=json.dumps(payload).encode(), headers=headers)
    with urlopen(request, timeout=120) as response:
        return json.load(response)


def run_agent(service, utterance, output, complete, context=None, max_turns=8, search_preview=True, image_packing='montage', tool_executor=None, tool_definitions=None, system_prompt=None, scene_context=None, room_evidence=None, decision_schema=None, native_only=False):
    """complete(messages) supplies one raw chat-completions response; no retries/fallback."""
    if image_packing not in ('montage', 'single'):
        raise ValueError('Unknown candidate image packing')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    use_descriptions = search_preview and service.descriptions is not None
    decision_schema = decision_schema if decision_schema is not None else read_json(SCHEMAS / 'grounding_decision.json')
    tools = tool_definitions if tool_definitions is not None else model_tools(search_preview, use_descriptions, decision_schema)
    room_evidence = room_evidence or {}
    known_rooms = {r['room_id'] for r in scene_context['data']['rooms']} if scene_context is not None else set()
    require(known_rooms <= service.store.rooms.keys(), 'Unknown room in initial context')
    if room_evidence:
        require(scene_context is not None and scene_context['snapshot_id'] == service.store.scene['snapshot_id'],
                'Area evidence requires bound scene context')
        cards = {c['card_id']: c for c in scene_context['data']['semantic_cards']}
        for cid, rooms in room_evidence.items():
            require(cid in cards and rooms == {cards[cid]['room_id']} and rooms <= service.store.rooms.keys()
                    and bool(cards[cid]['main_objects'] or cards[cid]['distinctive_features']),
                    'Area evidence must be supplied by a nonempty card')
    write_json(output / 'protocol.json', dict(tools=tools,
               max_turns=max_turns, search_preview=search_preview, image_packing=image_packing,
               montage_max_width=service.renderer.montage_max_width,
               single_max_width=service.renderer.single_max_width, use_descriptions=use_descriptions, decision_schema=decision_schema))
    prompt = system_prompt if system_prompt is not None else PROMPT
    prompt = prompt if search_preview else prompt.replace(
        '优先直接 search_candidates 获取候选及物体图片，根据图片判断。\n图片足够清楚时直接提交；只有细节不足或需要第二视角时调用 inspect_objects。',
        '优先直接 search_candidates 检索候选，inspect_objects 查看物体图片。')
    if use_descriptions:
        prompt = prompt.replace('先看最相关候选的图片，足以判断后直接提交；仅在具体歧义需要时翻页、换视角或补充空间图。',
            '先读最相关候选的描述，足以判断后直接提交；仅在关键细节不足时查看物体图片、翻页或补充空间图。')
        prompt = prompt.replace('优先直接 search_candidates 获取候选及物体图片，根据图片判断。',
            '优先search_candidates获取候选物体的外观描述，根据描述和用户条件判断；描述缺少关键细节时才inspect_objects查看物体图片。')
        prompt = prompt.replace('图片足够清楚时直接提交；只有细节不足或需要第二视角时调用 inspect_objects。',
            '描述足以判断时直接提交；只有描述缺少影响选择的细节时才调用inspect_objects查看物体图片。')
        prompt = prompt.replace('根据物体图片判断颜色、形状、摆放和可见状态',
            '根据物体描述或按需查看的图片判断颜色、形状、摆放和可见状态')
        prompt = prompt.replace('evidence_ids 引用对应物体图片的 media_id',
            'evidence_ids 引用对应物体描述的description_id或物体图片的media_id')
    messages = [dict(role='system', content=prompt)]
    if scene_context is not None:
        messages.append(dict(role='user', content=json.dumps(dict(scene_context=scene_context), ensure_ascii=False)))
        write_json(output / 'scene-model-context.json', scene_context)
    messages.append(dict(role='user', content=json.dumps(dict(utterance=utterance,
                         context=context if context is not None else {}), ensure_ascii=False)))
    known, supported_ids, evidence = set(), set(), {}
    started, timings = perf_counter(), []
    for turn in range(max_turns):
        # Full wire messages include actual image bytes, not just URI strings; no auth headers.
        write_json(output / f'{turn:02d}-request.json', messages)
        tick = perf_counter()
        try:
            response = complete(messages)
        finally:
            timings.append(dict(turn=turn, stage='model', seconds=perf_counter() - tick))
            write_json(output / 'timings.json', dict(events=timings, elapsed_seconds=perf_counter() - started))
        write_json(output / f'{turn:02d}-response.json', response)
        choice = response['choices'][0]
        message = choice['message']
        native = choice['finish_reason'] == 'tool_calls'
        if native_only and not native:
            raise ValueError('Agent must submit a native tool call, including finish')
        if choice['finish_reason'] not in ('stop', 'tool_calls'):
            raise ValueError('Model response incomplete; no decision accepted')
        if native:
            calls = message.get('tool_calls', [])
            if not calls or len(calls) > 4 or len({c['id'] for c in calls}) != len(calls):
                raise ValueError('Invalid native tool call batch')
            actions = [dict(action='tool', tool=c['function']['name'],
                            arguments=json.loads(c['function']['arguments'])) for c in calls]
            if any(a['tool'] == 'finish' for a in actions):
                if len(actions) != 1:
                    raise ValueError('finish must be the only call in its turn')
                actions = [dict(action='finish', decision=actions[0]['arguments'])]
            messages.append(dict(role='assistant', content=message.get('content'), tool_calls=calls))
            if 'provider_items' in message:
                messages[-1]['provider_items'] = message['provider_items']
        else:
            content = message['content']
            actions = [json.loads(content)]
            messages.append(dict(role='assistant', content=content))
        action = actions[0]
        if action.get('action') == 'finish':
            if set(action) != {'action', 'decision'}:
                raise ValueError('Unexpected final action fields')
            decision = action['decision']
            validate(decision, decision_schema)
            ids, refs = set(decision['object_ids']), set(decision['evidence_ids'])
            rooms = set(decision.get('room_ids', []))
            excluded = set(decision.get('excluded_ids', []))
            if ids & excluded or not excluded <= known:
                raise ValueError('Excluded IDs must be seen and separate from candidates')
            decision.setdefault('excluded_ids', [])
            if not ids <= known or not rooms <= known_rooms or not refs <= evidence.keys() | room_evidence.keys():
                raise ValueError('Decision cites unseen objects or evidence')
            if decision['status'] == 'resolved':
                supported = set().union(*(evidence.get(r, set()) for r in refs)) if refs else set()
                if not ids <= supported_ids or not ids <= supported:
                    raise ValueError('Resolved object lacks supplied image evidence or description')
                supported_rooms = set().union(*(room_evidence.get(r, set()) for r in refs)) if refs else set()
                if ids:
                    supported_rooms = set().union(*(set(service.store.objects[oid]['room_ids']) for oid in ids))
                if not rooms <= supported_rooms:
                    raise ValueError('Resolved area lacks card evidence or disagrees with the object area')
            result = dict(scene_id=service.store.scene['scene_id'], snapshot_id=service.store.scene['snapshot_id'],
                          grounding=decision, selection=dict(
                              selected_id=next(iter(ids)) if decision['status'] == 'resolved' and ids else None,
                              candidate_ids=decision['object_ids'], excluded_ids=decision['excluded_ids']), navigation=dict(status='blocked', goal=None,
                          reason='No verified navigation binding'), handoff='not_ready')
            if 'room_ids' in decision:
                result['selection'].update(selected_room_id=next(iter(rooms)) if decision['status'] == 'resolved' and rooms else None,
                                           candidate_room_ids=decision['room_ids'])
            write_json(output / 'result.json', result)
            return result
        image_parts = []
        for index, action in enumerate(actions):
            if action.get('action') != 'tool' or set(action) != {'action', 'tool', 'arguments'}:
                raise ValueError('Invalid agent action')
            definition = next((t['function'] for t in tools if t['function']['name'] == action['tool']), None)
            if definition is None:
                raise ValueError('Unknown model tool')
            validate(action['arguments'], definition['parameters'])
            arguments = dict(action['arguments'])
            arguments.pop('page', None)
            arguments.pop('view', None)
            arguments.pop('include_types', None)
            tick = perf_counter()
            from .preview import call_for_model
            if search_preview:
                execute = tool_executor if tool_executor is not None else call_for_model
                result, context, model_media, preview = execute(service, action['tool'], action['arguments'], image_packing)
            else:
                result = service.call(action['tool'], arguments)
                context = model_context(result.envelope, action['arguments'])
                model_media, preview = result.media, None
            timings.append(dict(turn=turn, stage='tool', tool=action['tool'], seconds=perf_counter() - tick))
            write_json(output / 'timings.json', dict(events=timings, elapsed_seconds=perf_counter() - started))
            suffix = f'-{index:02d}' if native else ''
            write_json(output / f'{turn:02d}{suffix}-arguments.json', action['arguments'])
            write_json(output / f'{turn:02d}{suffix}-tool.json', result.envelope)
            if preview is not None:
                audit_kind = 'descriptions' if preview.get('kind') == 'descriptions' else 'preview'
                write_json(output / f'{turn:02d}{suffix}-{audit_kind}.json', preview)
            data = context['data'] or {}
            for candidate in data.get('candidates', []):
                description = candidate.get('visual_description')
                if description:
                    evidence[description['description_id']] = {candidate['object_id']}
                    supported_ids.add(candidate['object_id'])
            known.update(o['object_id'] for o in data.get('candidates', []) + data.get('objects', []))
            write_json(output / f'{turn:02d}{suffix}-model-context.json', context)
            parts = [dict(type='text', text=json.dumps(context, ensure_ascii=False))]
            visible = {m['media_id'] for m in context['media']}
            for media in model_media:
                meta = media.metadata
                if meta['media_id'] not in visible:
                    continue
                evidence[meta['media_id']] = set(meta['object_ids'])
                if action['tool'] in ('inspect_objects', 'search_candidates'):
                    supported_ids.update(meta['object_ids'])
                encoded = base64.b64encode(media.content).decode()
                title = '物体图片' if action['tool'] in ('inspect_objects', 'search_candidates') else '空间图'
                parts.append(dict(type='text', text=title + ' media_id=' + meta['media_id']))
                parts.append(dict(type='image_url', image_url=dict(url=f"data:{meta['mime_type']};base64,{encoded}")))
            if native:
                messages.append(dict(role='tool', tool_call_id=calls[index]['id'], content=parts[0]['text']))
                image_parts.extend(parts[1:])
            else:
                messages.append(dict(role='user', content=parts))
        if image_parts:
            messages.append(dict(role='user', content=image_parts))
    raise ValueError('Agent turn budget exhausted; no decision accepted')
