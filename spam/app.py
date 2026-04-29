import os
import json
import torch
import html2text
from transformers import AutoModelForSequenceClassification, AutoTokenizer, AutoConfig

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



def lambda_handler(event, context):
    texts = _split_group_lines(h.handle(event['body']))
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
