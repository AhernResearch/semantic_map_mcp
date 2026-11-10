"""Bounded, read-only visual grounding loop using a local chat-completions endpoint."""
import base64
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

from jsonschema import validate

from .store import SCHEMAS, read_json, write_json

PROMPT = '''你是语义地图助手，负责根据用户描述找到目标物体。
用 list_scene 了解区域和类型，search_candidates 检索候选，inspect_objects 查看物体图片。
可用中文类别和英文外观描述检索；需要空间布局时调用 view_map。
根据物体图片判断颜色、形状、摆放和可见状态，并结合用户条件选择目标。
本任务以地图图片呈现的场景为查询范围，用户描述的占用等状态条件按图片中的可见状态匹配。
有明确匹配就选择；只有影响选择的具体歧义才追问。说明简短、具体。
调用 finish 提交结果：status、object_ids、evidence_ids、reason、question。
选定一个目标用 resolved，evidence_ids 引用对应物体图片的 media_id；无需追问时 question=null。
多个目标难以区分用 ambiguous，缺少图片或相关信息用 insufficient_evidence，缺参照上下文用 needs_context，取消用 cancelled。
'''

# Keep acquisition/provenance metadata in raw traces, outside the model's task context.
AUDIT_FIELDS = {'source_kind', 'source_refs', 'source_links', 'provenance', 'identity_status',
                'limitations', 'uri'}


def model_context(value, arguments=None):
    def clean(item):
        if isinstance(item, dict):
            return {k: clean(v) for k, v in item.items() if k not in AUDIT_FIELDS}
        if isinstance(item, list):
            return [clean(v) for v in item]
        return item
    result = clean(value)
    args = arguments or {}
    data = result.get('data')
    if not data:
        return result
    if result['tool'] == 'search_candidates':
        candidates = sorted(data['candidates'], key=lambda c: (
            -max((s['fusion_score'] or 0 for s in c['scores']), default=0), c['object_id']))
        start = (args.get('page', 1) - 1) * 5
        page = candidates[start:start + 5]
        result['data'] = dict(candidates=[dict(object_id=c['object_id'], label=c['label'],
            room_ids=c['room_ids'], matched_queries=c['matched_queries']) for c in page],
            total_candidates=len(candidates), next_page=args.get('page', 1) + 1 if start + 5 < len(candidates) else None,
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


def model_tools():
    definitions = read_json(SCHEMAS / 'tools.json')['tools']
    for t in definitions:
        if t['name'] == 'search_candidates':
            t['inputSchema']['properties']['page'] = dict(type='integer', minimum=1)
            t['description'] += ' 每页5个候选，用相同查询和page翻页；next_page为空表示本次召回池已读完。'
        if t['name'] == 'inspect_objects':
            t['inputSchema']['properties']['ids']['maxItems'] = 3
            t['inputSchema']['properties']['view'] = dict(type='integer', minimum=1, maximum=2)
            t['description'] += ' 每次最多3个对象，每个默认1张图片；需要另一视角时传view=2。'
    functions = [dict(type='function', function=dict(name=t['name'],
                 description=t.get('description', ''), parameters=t['inputSchema'])) for t in definitions]
    decision_parameters = read_json(SCHEMAS / 'grounding_decision.json')
    # Anthropic rejects top-level allOf; full conditional validation stays in the host.
    decision_parameters.pop('allOf')
    functions.append(dict(type='function', function=dict(name='finish',
        description='提交选定物体或需要澄清的问题。',
        parameters=decision_parameters)))
    return functions


def chat(endpoint, model, messages):
    headers = {'Content-Type': 'application/json'}
    if token := os.environ.get('MAP_AGENT_API_KEY'):
        headers['Authorization'] = 'Bearer ' + token
    payload = dict(model=model, messages=messages, tools=model_tools(), temperature=0, max_tokens=1600)
    request = Request(endpoint.rstrip('/') + '/chat/completions',
                      data=json.dumps(payload).encode(), headers=headers)
    with urlopen(request, timeout=120) as response:
        return json.load(response)


def run_agent(service, utterance, output, complete, context=None, max_turns=8):
    """complete(messages) supplies one raw chat-completions response; no retries/fallback."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / 'protocol.json', dict(tools=model_tools(), temperature=0,
               max_tokens=1600, max_turns=max_turns, decision_schema=read_json(SCHEMAS / 'grounding_decision.json')))
    messages = [dict(role='system', content=PROMPT),
                dict(role='user', content=json.dumps(dict(utterance=utterance, context=context or {}), ensure_ascii=False))]
    known, pictured, evidence = set(), set(), {}
    decision_schema = read_json(SCHEMAS / 'grounding_decision.json')
    for turn in range(max_turns):
        # Full wire messages include actual image bytes, not just URI strings; no auth headers.
        write_json(output / f'{turn:02d}-request.json', messages)
        response = complete(messages)
        write_json(output / f'{turn:02d}-response.json', response)
        choice = response['choices'][0]
        message = choice['message']
        native = choice['finish_reason'] == 'tool_calls'
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
            excluded = set(decision.get('excluded_ids', []))
            if ids & excluded or not excluded <= known:
                raise ValueError('Excluded IDs must be seen and separate from candidates')
            decision.setdefault('excluded_ids', [])
            if not ids <= known or not refs <= evidence.keys():
                raise ValueError('Decision cites unseen objects or evidence')
            if decision['status'] == 'resolved':
                supported = set().union(*(evidence[r] for r in refs)) if refs else set()
                if not ids <= pictured or not ids <= supported:
                    raise ValueError('Resolved object lacks supplied image evidence')
            result = dict(scene_id=service.store.scene['scene_id'], snapshot_id=service.store.scene['snapshot_id'],
                          grounding=decision, selection=dict(
                              selected_id=next(iter(ids)) if decision['status'] == 'resolved' else None,
                              candidate_ids=decision['object_ids'], excluded_ids=decision['excluded_ids']), navigation=dict(status='blocked', goal=None,
                          reason='No verified navigation binding'), handoff='not_ready')
            write_json(output / 'result.json', result)
            return result
        image_parts = []
        for index, action in enumerate(actions):
            if action.get('action') != 'tool' or set(action) != {'action', 'tool', 'arguments'}:
                raise ValueError('Invalid agent action')
            definition = next((t['function'] for t in model_tools() if t['function']['name'] == action['tool']), None)
            if definition is None:
                raise ValueError('Unknown model tool')
            validate(action['arguments'], definition['parameters'])
            arguments = dict(action['arguments'])
            arguments.pop('page', None)
            arguments.pop('view', None)
            result = service.call(action['tool'], arguments)
            suffix = f'-{index:02d}' if native else ''
            write_json(output / f'{turn:02d}{suffix}-tool.json', result.envelope)
            context = model_context(result.envelope, action['arguments'])
            data = context['data'] or {}
            known.update(o['object_id'] for o in data.get('candidates', []) + data.get('objects', []))
            write_json(output / f'{turn:02d}{suffix}-model-context.json', context)
            parts = [dict(type='text', text=json.dumps(context, ensure_ascii=False))]
            visible = {m['media_id'] for m in context['media']}
            for media in result.media:
                meta = media.metadata
                if meta['media_id'] not in visible:
                    continue
                evidence[meta['media_id']] = set(meta['object_ids'])
                if action['tool'] == 'inspect_objects':
                    pictured.update(meta['object_ids'])
                encoded = base64.b64encode(media.content).decode()
                title = '物体图片' if action['tool'] == 'inspect_objects' else '空间图'
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
