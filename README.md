# 业余无线电刷题网站

## 本地/服务器运行

安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

启动服务：

```bash
python3 server.py
```

访问：

```text
http://127.0.0.1:8002/
```

首次启动且数据库里没有用户和邀请码时，终端会打印一个 `Initial invite code`。用这个邀请码注册第一个账号。

## 环境变量

```bash
export SESSION_SECRET="replace-with-long-random-secret"
export LLM_BASE_URL="https://api.modelarts-maas.com"
export LLM_API_PATH="/openai/v1/chat/completions"
export LLM_API_KEY="your-maas-api-key"
export LLM_MODEL="deepseek-v3.2"
export LLM_PRECACHE_ON_START="1"
```

可选：

```bash
export DATABASE_PATH="data/app.db"
export INVITE_CODES="code1,code2"
export COOKIE_SECURE="1"
```

部署到 HTTPS 域名后建议设置 `COOKIE_SECURE=1`。

`LLM_PRECACHE_ON_START=1` 时，服务启动后会在后台按题目预生成 AI 解析并写入服务器缓存；没有配置 `LLM_API_KEY` 时不会启动。设置为 `0` 可关闭启动时预生成，管理员登录后仍可在“进度”页手动启动。

## 更新 A、B、C 题库

导入工具使用 PyMuPDF。在包含三份分类题库和《总题库附图标记.pdf》的目录上运行：

```bash
python3 -m pip install PyMuPDF
python3 tools/parse_pdf.py 20250809124154_2851 .
```

A 类使用 `questions.json`，B、C 类使用 `questions-B.json`、`questions-C.json`。三库共用 `figures/`。升级专项按原题号排除前置类别，学习记录按账号和类别独立保存。

## 管理员

在服务端私有启动环境中，用 `HAM_ADMIN_USERNAME` 指定一个已注册的站点账号。该账号登录后可从页头进入「管理」。未设置时，所有账号均为普通用户。在「解析模型」选择 OpenAI 或 DeepSeek，输入该厂商的 API Key，等待模型列表加载后选择模型并点击「保存并启用」。两家厂商各自保存设置，同一时间启用一家；首次启用前使用原环境变量配置。解析缓存按厂商、模型和题号区分。管理页还可生成单次注册邀请码。

服务端把厂商设置保存在运行用户的 `~/.config/ham-radio-exam-trainer/llm.sqlite3`，权限为 0600。可用 `HAM_LLM_SETTINGS_PATH` 指定独立文件。该文件含密钥，应按私有配置备份。
