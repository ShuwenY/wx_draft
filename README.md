# 微信公众号草稿箱接口服务

基于 Flask 搭建的微信公众号草稿箱 API，用于对接 Coze 智能体与微信公众平台，自动创建图文草稿。

## 项目结构

```
wx_draft/
├── app.py              # Flask 应用主入口
├── requirements.txt    # Python 依赖
├── Dockerfile          # Docker 镜像构建文件
├── .env.example        # 环境变量模板
├── .gitignore          # Git 忽略规则
└── README.md
```

## 快速开始

### 1. 环境准备

- Python 3.10+
- 微信公众号 AppID 和 AppSecret
- 在微信公众平台「安全中心 → IP 白名单」中配置服务器出口 IP

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置环境变量

复制模板并填入真实密钥：

```bash
cp .env.example .env
```

编辑 `.env`：

```
WX_APPID=你的AppID
WX_APPSECRET=你的AppSecret
PORT=5000
```

### 4. 启动服务

**本地开发：**

```bash
python app.py
```

**生产环境（gunicorn）：**

```bash
gunicorn -b 0.0.0.0:${PORT} app:app
```

**Docker 部署：**

```bash
docker build -t wx-draft .
docker run -p 5000:5000 -e WX_APPID=xxx -e WX_APPSECRET=xxx wx-draft
```

## API 接口

### 健康检查

```
GET /health
```

响应：

```json
{ "status": "ok" }
```

### 创建草稿

```
POST /wechat/draft
Content-Type: application/json; charset=utf-8
```

**请求参数：**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `title` | string | 是 | - | 文章标题 |
| `content_html` | string | 是 | - | 微信兼容 HTML 正文 |
| `cover_image_url` | string | 是 | - | 公网可访问的封面图 URL |
| `author` | string | 否 | `老幺` | 作者名称 |
| `digest` | string | 否 | `""` | 文章摘要 |
| `content_source_url` | string | 否 | `""` | 原文链接 |

**请求示例：**

```json
{
  "title": "测试文章标题",
  "author": "老幺",
  "digest": "这是一篇测试文章摘要",
  "content_html": "<h1>Hello World</h1><p>正文内容</p>",
  "cover_image_url": "https://example.com/cover.jpg",
  "content_source_url": "https://example.com"
}
```

**成功响应（200）：**

```json
{
  "item": [],
  "media_id": "b0QOK-qWRy85_Mh3ADcQVth6YXltvxXoRzF72y7iJ2M5LXfurF9V4QDxjf9Ukhjd"
}
```

**错误响应：**

| 状态码 | errcode | 说明 |
|--------|---------|------|
| 400 | -1 | 缺少必填字段（title / content_html / cover_image_url） |
| 400 | 微信 errcode | 微信 API 返回的业务错误 |
| 500 | -1 | 服务器内部错误或第三方 API 调用失败 |

## 架构流程

```
POST /wechat/draft
    │
    ├─ 1. 校验必填字段
    │
    ├─ 2. 获取 access_token（内存缓存，提前 300s 刷新）
    │
    ├─ 3. 下载封面图片
    │
    ├─ 4. 上传图片到微信永久素材库 → 获得 thumb_media_id
    │
    └─ 5. 调用 draft/add 创建草稿 → 返回 media_id
```

## 本地测试

### curl（Linux / macOS）

```bash
# 健康检查
curl http://localhost:5000/health

# 创建草稿
curl -X POST http://localhost:5000/wechat/draft \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{
    "title": "测试文章",
    "author": "老幺",
    "digest": "摘要",
    "content_html": "<h1>你好</h1><p>正文</p>",
    "cover_image_url": "https://picsum.photos/400/300"
  }'
```

### PowerShell（Windows）

```powershell
$body = @{
    title = "测试文章"
    author = "老幺"
    digest = "摘要"
    content_html = "<h1>你好</h1><p>正文</p>"
    cover_image_url = "https://picsum.photos/400/300"
} | ConvertTo-Json

$utf8Bytes = [System.Text.Encoding]::UTF8.GetBytes($body)

Invoke-WebRequest -Uri http://localhost:5000/wechat/draft `
  -Method Post -Body $utf8Bytes `
  -ContentType "application/json; charset=utf-8" -UseBasicParsing
```

> **注意**：PowerShell 5.1 需用 `[System.Text.Encoding]::UTF8.GetBytes()` 将 JSON 转为 UTF-8 字节数组发送，否则中文会乱码。

## 微信云托管部署

本项目已适配微信云托管：

- 监听端口来自环境变量 `PORT`（默认 5000）
- HTTPS 终止在外层，容器内使用 HTTP
- 使用 `gunicorn` 作为 WSGI 服务器
- 不在本地写入文件，所有图片素材通过内存流上传

## 约束

- 不涉及群发功能，仅创建草稿
- 封面图必须为公网可访问的 URL（支持 jpg / png / gif / bmp）
- access_token 使用内存缓存，多 worker 部署时各 worker 独立缓存