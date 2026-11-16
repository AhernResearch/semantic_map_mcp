"""Export existing Agent logs as an offline Chinese trace viewer plus unchanged raw files."""
import argparse
import base64
import hashlib
import html
import json
import shutil
from pathlib import Path

from sementic_map_mcp.store import read_json, write_json

STATUS = {'resolved': '模型选出一个对象（待核实）', 'ambiguous': '有多个可能，需要追问',
          'insufficient_evidence': '证据不足，不能确定', 'needs_context': '缺少上下文，需要补充',
          'cancelled': '已取消'}
NAMES = {'list_scene': '查看场景里有哪些区域和物体类型', 'search_candidates': '检索可能符合描述的对象',
         'inspect_objects': '查看指定对象的详情和图片', 'view_map': '查看候选的空间简图',
         'finish': '提交最终判断'}
CSS = '''body{margin:0;background:#f3f5f8;color:#192635;font:16px/1.7 system-ui,sans-serif}
main{max-width:1100px;margin:auto;padding:28px}a{color:#155fa8}h1{font-size:30px;line-height:1.3}h2{font-size:23px}
.card,article{background:white;border:1px solid #dce2ea;border-radius:12px;padding:24px;margin:20px 0}
.note{color:#536274}.badge{background:#e9f1fc;padding:4px 10px;border-radius:6px;display:inline-block}
.answer{border-left:5px solid #2c7299}.question{background:#fff4d5;padding:16px;border-radius:8px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.6 monospace;max-height:500px;overflow:auto;background:#f4f6f8;padding:14px}
details{margin:12px 0}summary{cursor:pointer;font-weight:600}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{border-bottom:1px solid #e2e7ed;padding:9px;text-align:left;vertical-align:top}figure{margin:20px 0}
img{max-width:100%;height:auto;border:1px solid #dce2ea}figcaption{font-size:14px;color:#526173}
.preserve{white-space:pre-wrap}.links{display:flex;gap:16px;flex-wrap:wrap} .scroll{overflow:auto}
@media(max-width:650px){main{padding:12px}.card,article{padding:15px}h1{font-size:25px}}
'''


def esc(value):
    return html.escape(str(value))


def dump(value):
    return esc(json.dumps(value, ensure_ascii=False, indent=2))


def detail(title, value):
    return f'<details><summary>{esc(title)}</summary><pre>{dump(value)}</pre></details>'


def page(title, body):
    return f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title><style>{CSS}</style><main>{body}</main></html>'


def table(headers, rows):
    return '<div class="scroll"><table><thead><tr>' + ''.join(f'<th>{esc(x)}</th>' for x in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(f'<td>{esc(x)}</td>' for x in row) + '</tr>' for row in rows) + '</tbody></table></div>'


def tool_summary(envelope):
    data = envelope['data']
    if data is None:
        return '<p>工具没有返回有效数据。</p>'
    tool = envelope['tool']
    if tool == 'list_scene':
        return (f'<p>共 {data["object_count"]} 条对象记录；记录数不等于独立实物数。</p>' +
                table(['区域 ID', '名称', '登记对象数'], [(r['room_id'], r['name'], r['object_count']) for r in data['rooms']]) +
                detail('全部可查询类型', data['semantic_types']))
    if tool in ('search_candidates', 'view_map'):
        labels = {c['object_id']: c['label'] for c in data['candidates']}
        groups = ''.join(f'<p><b>查询：{esc(g["query"])}</b><br>返回顺序：' +
                         esc(' → '.join(f'{i}（{labels[i]}）' for i in g['candidate_ids'])) + '</p>' for g in data.get('groups', []))
        return f'<p>返回 {len(labels)} 个候选。下面是源标签和检索结果，不是已确认答案。</p>' + groups + table(
            ['对象 ID', '源标签', '源区域', '命中的查询'],
            [(c['object_id'], c['label'], ', '.join(c['room_ids']), ', '.join(c['matched_queries'])) for c in data['candidates']])
    return table(['对象 ID', '源标签', '源区域', '中心坐标（非导航站位）'],
                 [(o['object_id'], o['label'], ', '.join(o['room_ids']), str(o['center_xyz'])) for o in data['objects']])


def export(run, output, histories):
    output.mkdir(parents=True, exist_ok=False)
    for source in [run, *histories]:
        shutil.copytree(source, output / 'raw' / source.name)
    report = read_json(run / 'report.json')
    index = [f'<h1>语义地图 Agent：{len(report["cases"])} 条指令的完整过程</h1>',
             '<p>先选一条指令。每页先给最终回答，再按顺序展开：模型调用什么工具、地图返回什么、实际给模型看了哪些图片。</p>',
             '<p class="note">这是一次真实模型运行的记录，不是人工标准答案。记录包含服务返回的消息、工具与图像，不声称包含模型内部思考。所有导航目标均为空，未执行机器人运动。</p>',
             f'<p>模型：<b>{esc(report["model"])}</b> · <a href="raw/{esc(run.name)}/report.json">原始批次结果 JSON</a></p>']
    verification = []
    for row in report['cases']:
        cid = row['case_id']
        folder = run / cid
        request = read_json(folder / '00-request.json')
        user = json.loads(request[1]['content'])
        result = row.get('result')
        final = result['grounding'] if result else None
        status = STATUS[final['status']] if final else '执行失败'
        root = f'raw/{run.name}/{cid}'
        index.append(f'<section class="card"><span class="badge">{esc(status)}</span><h2><a href="{esc(cid)}.html">{esc(user["utterance"])}</a></h2><p>{esc(cid)} · 点击查看完整过程和图片</p></section>')
        body = [f'<a href="index.html">← 返回指令列表</a><h1>{esc(user["utterance"])}</h1><p>{esc(cid)} · {esc(status)}</p>']
        if final:
            body += ['<section class="card answer"><h2>最后模型怎么回答？</h2>',
                     f'<p><b>{esc(status)}</b></p><p>返回的候选或讨论对象：{esc(", ".join(final["object_ids"]) or "无")}</p>',
                     f'<p class="preserve">{esc(final["reason"])}</p>']
            if final['question']:
                body.append(f'<div class="question"><b>模型向用户追问：</b><br>{esc(final["question"])}</div>')
            body += ['<p class="note">这是模型判断。除“选出一个对象”外，返回的 ID 也可能是讨论或排除的对象，不能视为选定目标。导航目标：空。</p>',
                     detail('完整最终结果 JSON', result), '</section>']
        else:
            body.append(f'<p>失败原因：{esc(row.get("error", ""))}</p>')
        body += ['<h2>按发生顺序查看</h2>', detail('起点：用户原始输入与上下文', user),
                 '<p class="note">下面展示工具调用、返回的候选和物体图片。图片中保留全景与物体细节，点击可查看原图。</p>']
        sent = set()
        for p in folder.glob('*-request.json'):
            for m in read_json(p):
                if not isinstance(m.get('content'), list):
                    continue
                for part in m['content']:
                    if part['type'] == 'image_url':
                        sent.add(hashlib.sha256(base64.b64decode(part['image_url']['url'].split(',', 1)[1])).hexdigest())
        for p in sorted(folder.glob('*-response.json')):
            turn = p.name.split('-')[0]
            response = read_json(p)
            message = response['choices'][0]['message']
            body.append(f'<article><h2>第 {int(turn)+1} 轮</h2>')
            if message.get('content'):
                body.append(f'<p class="preserve"><b>模型返回的文字：</b><br>{esc(message["content"])}</p>')
            for call in message.get('tool_calls', []):
                name = call['function']['name']
                body.append(f'<h3>{esc(NAMES.get(name, name))} <small>({esc(name)})</small></h3>')
                body.append(detail('模型传入的完整参数', json.loads(call['function']['arguments'])))
            for toolfile in sorted(folder.glob(f'{turn}-*tool.json')):
                envelope = read_json(toolfile)
                body.append(f'<h3>地图返回：{esc(NAMES[envelope["tool"]])}</h3>')
                context_file = toolfile.with_name(toolfile.name.replace('-tool.json', '-model-context.json'))
                shown = read_json(context_file) if context_file.exists() else envelope
                body.append(tool_summary(shown))
                if shown['data'] and 'next_page' in shown['data']:
                    body.append(f'<p>下一页：{esc(shown["data"]["next_page"])} · 召回池共 {shown["data"]["total_candidates"]} 个候选</p>')
                if envelope['errors']:
                    body.append(detail('工具报告的问题', envelope['errors']))
                preview_file = toolfile.with_name(toolfile.name.replace('-tool.json', '-preview.json'))
                audit_media = envelope['media'] + (read_json(preview_file)['media'] if preview_file.exists() else [])
                for media in shown['media']:
                    source = run / 'media' / Path(next(m['uri'] for m in audit_media if m['media_id'] == media['media_id'])).name
                    digest = hashlib.sha256(source.read_bytes()).hexdigest()
                    if digest not in sent:
                        raise ValueError(f'{cid}: media was not supplied in recorded model requests: {media["media_id"]}')
                    verification.append(dict(case_id=cid, media_id=media['media_id'], sha256=digest, supplied=True))
                    url = f'raw/{run.name}/media/{source.name}'
                    caption = f'对象 {", ".join(media["object_ids"])} · 视图 {", ".join(media["observation_ids"])} · media_id {media["media_id"]}'
                    body.append(f'<figure><a href="{esc(url)}" target="_blank"><img loading="lazy" src="{esc(url)}" alt="{esc(caption)}"></a><figcaption>{esc(caption)}<br>已核对：这些图片字节确实出现在本用例发送给模型的请求中。点击查看原图。</figcaption></figure>')
                if context_file.exists():
                    body.append(detail('模型实际收到的工具文本', shown))
                body.append(detail('原始工具数据（含完整审计字段）', envelope))
            body += [detail('原始模型响应', response), f'<div class="links"><a href="{esc(root)}/{esc(turn)}-request.json">完整模型输入（含历史和图片字节）</a><a href="{esc(root)}/{esc(p.name)}">原始响应文件</a></div>', '</article>']
        for request_file in sorted(folder.glob('*-request.json')):
            turn = request_file.name.split('-')[0]
            if not (folder / f'{turn}-response.json').exists():
                body.append(f'<article><h2>第 {int(turn)+1} 轮：未收到完整响应</h2>'
                            f'<p>{esc(row.get("error", "调用未完成"))}</p>'
                            f'<a href="{esc(root)}/{esc(request_file.name)}">已发送的完整模型输入</a></article>')
        protocol_url = f'{root}/protocol.json' if (folder / 'protocol.json').exists() else f'raw/{run.name}/protocol.json'
        body += [detail('本用例系统提示词（原文）', request[0]['content']),
                 f'<p><a href="{esc(protocol_url)}">完整工具定义与校验契约</a> · <a href="index.html">返回首页</a></p>']
        (output / f'{cid}.html').write_text(page(user['utterance'], ''.join(body)))
    index += ['<section class="card"><h2>原始文件与之前的失败记录</h2><p>raw 目录保存原文件副本，未改写；HTML 是阅读视图。打开 manifest.json 可核对文件哈希。图片字节核对记录见 image_verification.json。</p>']
    for source in histories:
        r = read_json(source / 'report.json')
        index.append(f'<details><summary>{esc(source.name)}：历史试跑</summary>'+ table(['用例','执行状态','错误'],[(x['case_id'],x['execution'],x.get('error','')) for x in r['cases']])+f'<a href="raw/{esc(source.name)}/report.json">原始结果</a></details>')
    index.append('</section>')
    (output / 'index.html').write_text(page('语义地图 Agent 完整记录', ''.join(index)))
    write_json(output / 'image_verification.json', verification)
    manifest = {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(output.rglob('*')) if p.is_file()}
    write_json(output / 'manifest.json', manifest)
    archive = shutil.make_archive(str(output), 'zip', output.parent, output.name)
    print(output / 'index.html')
    print(archive)
    print(f'Verified {len(verification)} tool image references against actual model request bytes.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--history', nargs='*', type=Path, default=[])
    args = parser.parse_args()
    export(args.run.resolve(), args.output.resolve(), [p.resolve() for p in args.history])
