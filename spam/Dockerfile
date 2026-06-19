FROM public.ecr.aws/lambda/python:3.12

ARG MODEL_TAR_URL

RUN dnf install -y tar
COPY requirements.txt ./
RUN python3.12 -m pip install -r requirements.txt -t . && rm requirements.txt

ADD ${MODEL_TAR_URL}  ./model.tar
# chmod a+rX：tar 可能保留 CodeBuild umask 的 0600 權限，Lambda runtime 讀不到 → 補上全域讀權限
RUN mkdir ./model/ && tar -xf ./model.tar -C ./model/ && rm ./model.tar && chmod -R a+rX ./model/
COPY *.py ./

CMD ["app.lambda_handler"]
