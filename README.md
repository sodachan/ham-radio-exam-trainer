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

`LLM_PRECACHE_ON_START=1` 时，服务启动后会在后台按题目预生成 AI 解析并写入服务器缓存；没有配置 `LLM_API_KEY` 时不会启动。设置为 `0` 可关闭启动时预生成，登录后仍可在“进度”页手动启动。
