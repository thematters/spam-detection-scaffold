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



## Comment Model（留言 spam）

原管線針對「文章」訓練。留言（comment）spam 另起一套，因為現役文章模型套到留言
recall 僅 ~0.68（漏掉約 1/3 已被守望相助隊移除的留言 spam）。

留言模型不走 LoRA：留言 spam 高度模板化，一個 **e5-small embedding + logistic
regression head** 在「未見模板」的 leave-one-family-out 評估上即達 recall ~0.88、
誤殺（over-kill）僅 ~0.33%，且可在筆電 CPU 上幾秒訓完。

`trains/spam/` 內的留言腳本（依序）：

1. `harvest_community_watch.py` — 從公開 `communityWatchActions` 取**正樣本**
   （已移除的留言），以 contentHash 當 template-family。
2. `harvest_normal_comments.py` — 從公開 `search → Article.comments` 取**負樣本**
   （正常留言），排除已被守望相助隊處理者。
3. `build_comment_dataset.py` — 合併正負樣本，html2text 去標籤，做
   **family-grouped** train/holdout 切分（避免模板洩漏）。
4. `cheap_baselines.py` / `cv_eval.py` — 便宜優先 baseline 與 leave-one-family-out
   穩健評估。
5. `train_comment_head.py` — 用全資料訓練最終 logreg head，匯出可部署 tar
   （`./model/` 內含 SentenceTransformer + `head.json`）。

部署沿用既有流程：上傳 tar → `sam build --parameter-overrides SpamModelTarUrl=...`
→ `sam deploy`。推論端（`spam/infer.py`）載入 SentenceTransformer 並以 numpy 套用
logreg head，runtime 不需 sklearn。`baseline_lambda.py` 量測現役模型基準。
