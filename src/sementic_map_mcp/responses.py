# Derived from open-nav-harness; modified for standalone map service ownership.
# SPDX-License-Identifier: Apache-2.0
# See licenses/open-nav-harness-LICENSE and licenses/open-nav-harness-NOTICE.
"""Responses wire format; the navigation loop remains transport-independent."""

import copy


def request_input(messages, tools, *, replay_reasoning=True):
    items: list[dict] = []
    for message in messages:
        role = message['role']
        if role == 'assistant' and message.get('provider_items') is not None:
            # Stateless replay retains reasoning (including encrypted content) and call IDs.
            items.extend(copy.deepcopy(item) for item in message['provider_items']
                         if replay_reasoning or item.get('type') != 'reasoning')
            continue
        if role == 'tool':
            items.append({'type': 'function_call_output', 'call_id': message['tool_call_id'],
                          'output': message['content']})
            continue
        content = message.get('content')
        if content:
            if isinstance(content, str):
                parts = [{'type': 'input_text' if role != 'assistant' else 'output_text', 'text': content}]
            else:
                parts = []
                for part in content:
                    if part['type'] == 'text':
                        parts.append({'type': 'input_text', 'text': part['text']})
                    elif part['type'] == 'image_url':
                        parts.append({'type': 'input_image', 'image_url': part['image_url']['url'],
                                      'detail': part['image_url'].get('detail', 'auto')})
                    else:
                        raise ValueError('Unsupported Responses content: ' + part['type'])
            items.append({'role': role, 'content': parts})
        for call in message.get('tool_calls', []):
            items.append({'type': 'function_call', 'call_id': call['id'], **call['function']})
    functions = [{'type': 'function', **t['function'], 'strict': False} for t in tools]
    return {'input': items, 'tools': functions}


def response_message(body):
    text, calls = [], []
    for item in body.get('output', []):
        if item['type'] == 'function_call':
            calls.append({'id': item['call_id'], 'type': 'function',
                          'function': {'name': item['name'], 'arguments': item['arguments']}})
        elif item['type'] == 'message':
            for part in item.get('content', []):
                if part['type'] == 'output_text':
                    text.append(part['text'])
                elif part['type'] == 'refusal':
                    text.append(part['refusal'])
    return {'role': 'assistant', 'content': '\n'.join(text) or None, 'tool_calls': calls,
            'provider_items': copy.deepcopy(body.get('output', []))}


def normalize_response(body):
    if not isinstance(body, dict):
        raise ValueError('Responses endpoint did not return an object')
    if body.get('status') != 'completed' or body.get('error'):
        raise ValueError('Responses request did not complete: ' + str(
            body.get('error') or body.get('incomplete_details') or body.get('status')))
    usage = body.get('usage') or {}
    return {'id': body.get('id'), 'choices': [{'message': response_message(body), 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': usage.get('input_tokens', 0),
                      'completion_tokens': usage.get('output_tokens', 0),
                      'prompt_tokens_details': usage.get('input_tokens_details') or {},
                      'completion_tokens_details': usage.get('output_tokens_details') or {}}}
