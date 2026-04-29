import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification


CHECKPOINT = "./model/"
SEQUENCE_LENGTH = 512


tokenizer = AutoTokenizer.from_pretrained(CHECKPOINT)
model = AutoModelForSequenceClassification.from_pretrained(
    CHECKPOINT, torch_dtype="auto"
)
model.config.pad_token_id = tokenizer.pad_token_id
model.eval()


def infer(texts: list[str]):
    inputs = tokenizer(
        texts,
        padding="max_length",
        truncation=True,
        max_length=SEQUENCE_LENGTH,
        return_tensors="pt",
    )
    with torch.no_grad():
        outputs = model(**inputs)
        confidents = torch.softmax(outputs.logits, dim=-1)
        return [score.item() for score in confidents[:, 1]]
