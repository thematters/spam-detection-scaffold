FROM public.ecr.aws/lambda/python:3.12

ARG MODEL_TAR_URL

RUN dnf install -y tar
COPY requirements.txt ./
RUN python3.12 -m pip install -r requirements.txt -t . && rm requirements.txt

ADD ${MODEL_TAR_URL}  ./model.tar
RUN mkdir ./model/ && tar -xf ./model.tar -C ./model/ && rm ./model.tar
COPY *.py ./

CMD ["app.lambda_handler"]
