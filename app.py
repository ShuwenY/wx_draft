import os
import time
import json
import logging

import requests
import urllib3
from flask import Flask, request, jsonify
from dotenv import load_dotenv

# 云托管环境 SSL 代理兼容
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

load_dotenv()

app = Flask(__name__)
app.json.ensure_ascii = False  # 确保 JSON 响应中中文不被转义

# ---------- 配置 ----------
WX_APPID = os.environ.get("WX_APPID", "")
WX_APPSECRET = os.environ.get("WX_APPSECRET", "")
PORT = int(os.environ.get("PORT", 5000))

if not WX_APPID or not WX_APPSECRET:
    raise RuntimeError("缺少环境变量 WX_APPID 或 WX_APPSECRET，请检查 .env 文件或云托管环境变量配置")

# 微信云托管「开放接口服务 / 云调用」模式
# 开启后容器内通过 http://api.weixin.qq.com 调用，无需自行携带 access_token
USE_WX_CLOUD_CALL = os.environ.get("USE_WX_CLOUD_CALL", "0") == "1"
WX_API_BASE = "http://api.weixin.qq.com" if USE_WX_CLOUD_CALL else "https://api.weixin.qq.com"

# ---------- access_token 内存缓存 ----------
_token_cache = {"token": None, "expires_at": 0}

# ---------- 日志 ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

if USE_WX_CLOUD_CALL:
    logger.info("微信云托管「开放接口服务 / 云调用」模式已启用，API 基址: %s", WX_API_BASE)


def get_access_token() -> str:
    """获取微信 access_token（内存缓存，有效期 < 7200s）。
    云调用模式下不请求 token，返回空字符串（token 由云托管网关自动注入）。
    """
    if USE_WX_CLOUD_CALL:
        return ""

    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"] - 300:
        return _token_cache["token"]

    url = f"{WX_API_BASE}/cgi-bin/token"
    params = {
        "grant_type": "client_credential",
        "appid": WX_APPID,
        "secret": WX_APPSECRET,
    }
    resp = requests.get(url, params=params, timeout=10, verify=False)
    data = resp.json()

    if "errcode" in data and data["errcode"] != 0:
        raise RuntimeError(f"获取 access_token 失败: {data.get('errmsg', 'unknown')}")

    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = now + data["expires_in"]
    logger.info("access_token 已刷新")
    return _token_cache["token"]


def upload_image(token: str, image_url: str) -> str:
    """下载图片并作为永久素材上传到微信，返回 thumb_media_id。
    云调用模式下 token 参数为空，URL 不带 access_token。
    """
    logger.info("下载封面图片: %s", image_url)
    img_resp = requests.get(image_url, timeout=15, verify=False)
    if img_resp.status_code != 200:
        raise RuntimeError(f"下载封面图片失败 HTTP {img_resp.status_code}")

    content_type = img_resp.headers.get("Content-Type", "image/jpeg")
    ext_map = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/bmp": ".bmp",
    }
    ext = ext_map.get(content_type, ".jpg")

    if USE_WX_CLOUD_CALL:
        upload_url = f"{WX_API_BASE}/cgi-bin/material/add_material?type=image"
    else:
        upload_url = f"{WX_API_BASE}/cgi-bin/material/add_material?access_token={token}&type=image"

    files = {"media": (f"cover{ext}", img_resp.content, content_type)}
    resp = requests.post(upload_url, files=files, timeout=30, verify=False)
    data = resp.json()

    if "errcode" in data and data["errcode"] != 0:
        raise RuntimeError(
            f"上传图片素材失败: errcode={data['errcode']} errmsg={data.get('errmsg', '')}"
        )

    if "media_id" not in data:
        logger.error("上传图片素材响应异常: %s", json.dumps(data, ensure_ascii=False))
        raise RuntimeError(f"上传图片素材失败: 响应中缺少 media_id, 完整响应: {json.dumps(data, ensure_ascii=False)}")

    logger.info("图片素材上传成功, media_id=%s", data["media_id"])
    return data["media_id"]


def create_draft(token: str, payload: dict) -> dict:
    """调用微信 draft/add 接口创建草稿。
    云调用模式下 token 参数为空，URL 不带 access_token。
    """
    if USE_WX_CLOUD_CALL:
        url = f"{WX_API_BASE}/cgi-bin/draft/add"
    else:
        url = f"{WX_API_BASE}/cgi-bin/draft/add?access_token={token}"

    articles = [{
        "title": payload["title"],
        "author": payload.get("author", "老幺"),
        "digest": payload.get("digest", ""),
        "content": payload["content_html"],
        "content_source_url": payload.get("content_source_url", ""),
    }]

    if "thumb_media_id" in payload:
        articles[0]["thumb_media_id"] = payload["thumb_media_id"]

    body = {"articles": articles}
    logger.info("创建草稿: title=%s", payload["title"])

    resp = requests.post(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=15,
        verify=False,
    )
    data = resp.json()
    return data


# ---------- 路由 ----------

@app.route("/wechat/draft", methods=["POST"])
def wechat_draft():
    try:
        req = request.get_json(force=True)
        if not req:
            return jsonify({"errcode": -1, "errmsg": "请求体为空"}), 400

        # 校验必填字段
        for field in ("title", "content_html"):
            if field not in req:
                return jsonify({"errcode": -1, "errmsg": f"缺少必填字段: {field}"}), 400

        token = get_access_token()

        payload = {
            "title": req["title"],
            "author": req.get("author", "老幺"),
            "digest": req.get("digest", ""),
            "content_html": req["content_html"],
            "content_source_url": req.get("content_source_url", ""),
        }

        # 处理封面图片（thumb_media_id 是微信 draft/add 的必填字段）
        cover_url = req.get("cover_image_url", "")
        if cover_url:
            thumb_media_id = upload_image(token, cover_url)
            payload["thumb_media_id"] = thumb_media_id
        else:
            return jsonify({"errcode": -1, "errmsg": "缺少必填字段: cover_image_url（微信草稿必须提供封面图）"}), 400

        result = create_draft(token, payload)

        if result.get("errcode", 0) != 0:
            errcode = result.get("errcode", -999)
            errmsg = result.get("errmsg", "unknown")
            logger.error("草稿创建失败: errcode=%s errmsg=%s", errcode, errmsg)
            return jsonify({"errcode": errcode, "errmsg": errmsg}), 400

        logger.info(
            "草稿创建成功: media_id=%s",
            result.get("media_id", ""),
        )
        return jsonify(result)

    except RuntimeError as e:
        logger.error("业务异常: %s", e)
        return jsonify({"errcode": -1, "errmsg": str(e)}), 500
    except Exception as e:
        logger.exception("未预期的异常")
        return jsonify({"errcode": -1, "errmsg": f"服务器内部错误: {e}"}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "mode": "cloud_call" if USE_WX_CLOUD_CALL else "token",
        "api_base": WX_API_BASE,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)