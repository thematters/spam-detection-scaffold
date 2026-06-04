import os
import json
import html2text

from infer import infer


h = html2text.HTML2Text()
h.ignore_links = True
h.images_to_alt = True

def _split_group_lines(text, tokens = 2000):
    lines = text.splitlines(keepends=True)
    if len(lines) == 1:
        return lines
    outputs = []
    tmp = ""
    for line in lines:
        tmp = tmp + line
        if len(tmp) > tokens:
            outputs.append(tmp)
            tmp = ""
    else:
        if tmp and len(outputs) == 0:
            return [tmp]
        outputs[-1] = outputs[-1] + tmp
            
    return outputs



def _extract_text(body):
    """Accept either a raw text/HTML body or matters-server's JSON {"text": ...}.

    The matters-server SpamDetector posts JSON {text}; ad-hoc callers (curl) post
    raw text. Support both so the same endpoint works for the backend and tests.
    """
    if not body:
        return ""
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict) and "text" in parsed:
            return parsed["text"] or ""
    except (ValueError, TypeError):
        pass
    return body


def lambda_handler(event, context):
    texts = _split_group_lines(h.handle(_extract_text(event['body'])))
    scores = infer(texts)
    print('version: ', os.environ.get('AWS_LAMBDA_FUNCTION_VERSION'))
    print('scores: ', scores)
    return {
        'statusCode': 200,
        'body': json.dumps(
            {
                "score": max(scores),
            }
        )
    }
