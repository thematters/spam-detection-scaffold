# spam-detection-scaffold

這是一個 Serverless 的文章內容檢測服務。主要透過 HTTP API 傳入文章內容後，會回傳一個
介於 0 到 1 之間的分數，分數越高代表越可能是無效內容。



## Tech Stack

- 整體採用 AWS Lambda 容器映像搭配 API Gateway，透過 AWS SAM 進行管理與部署。
- 模型以 ibm-granite/granite-embedding-107m-multilingual 為基底，搭配 Hugging Face
  Transformers 與 LoRA 微調，最後將權重合併並打包為單一 tar 檔。
- 推論執行時使用 PyTorch CPU 版本。
- 訓練流程以 Jupyter notebook 完成，涵蓋資料處理、模型訓練、LoRA 合併與評估。
- 文字前處理使用 html2text，將 HTML 內容轉為純文字。



## Project Structure

根目錄：

- template.yaml
    - AWS SAM 部署模板，定義 Lambda 函式、API Gateway 路由與相關參數。
- samconfig.toml
    - SAM CLI 的預設參數設定，包含 AWS 區域、stack 名稱以及 Lambda 執行角色 ARN
      等參數。

主要資料夾：

- spam/
    - 執行時使用的容器映像相關檔案，包含 Dockerfile、Python 推論程式碼與
      requirements.txt。
- trains/spam/
    - 訓練模型的 Jupyter notebook，依編號順序執行，總共五份檔案，從資料合併
      到模型評估。



## Get Started

1. 進入 trains/spam/ 目錄，依編號順序執行 notebook：0.1 合併標籤、0.2 資料
   預處理、1 模型訓練、2 LoRA 合併與打包、3 評估與閾值掃描。完成後會得到一份
   spam_*.tar 檔。
2. 將該 tar 檔上傳到 docker build 環境可讀取的位置，例如以 S3 搭配 Signed URL，
   或是公開的 HTTPS 位址。
3. 執行 sam build 時，透過 parameter-overrides 傳入 SpamModelTarUrl，指向步驟 2 準備好的 tar URL。
4. 首次部署請使用 sam deploy --guided 進行設定，後續部署直接執行 sam deploy
   即可。
5. 部署完成後，從 CloudFormation stack 的輸出取得 InferenceApi 的 URL，將文字
   內容以 POST 方式送至 /spam/infer/，回應 JSON 中的 score 欄位即為判斷分數。



## API Example

部署完成後，可以直接以 curl 將文章內容透過 POST 送往端點：

```
curl -X POST https://<api-id>.execute-api.<region>.amazonaws.com/Prod/spam/infer/ \
     -d '這是要判斷的文章內容。'
```

回應為 JSON 格式，分數越接近 1 代表越可能是無效內容：

```
{"score": 0.987}
```
