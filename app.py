import os
import json
import asyncio
import requests
import aiohttp

from flask import Flask, request, jsonify
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from google.protobuf.json_format import MessageToJson

import like_pb2
import like_count_pb2
import uid_generator_pb2

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
API_KEY = "AX2"


# ---------- Token loader ----------
def _load_json_local(name):
    path = os.path.join(BASE_DIR, name)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_tokens(region: str, system: str = None):
    s = (region or "").upper()

    if s == "IND":
        return _load_json_local("token_ind.json")
    elif s in {"BR", "US", "SAC", "NA"}:
        return _load_json_local("token_br.json")
    else:
        if system == "1":
            return _load_json_local("token_bd_100.json")
        elif system == "2":
            return _load_json_local("token_bd_200.json")
        else:
            return _load_json_local("token_bd_200.json")


# ---------- Encryption / protobuf helpers ----------
def encrypt_message(plaintext_bytes: bytes) -> str:
    key = b"Yg&tc%DEuh6%Zc^8"
    iv = b"6oyZDr22E3ychjM%"
    cipher = AES.new(key, AES.MODE_CBC, iv)
    padded = pad(plaintext_bytes, AES.block_size)
    return cipher.encrypt(padded).hex()


def create_protobuf_message(user_id: str, region: str) -> bytes:
    msg = like_pb2.like()
    msg.uid = int(user_id)
    msg.region = region
    return msg.SerializeToString()


async def send_request(encrypted_hex: str, token: str, url: str):
    edata = bytes.fromhex(encrypted_hex)
    headers = {
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
        "Connection": "Keep-Alive",
        "Accept-Encoding": "gzip",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/x-www-form-urlencoded",
        "Expect": "100-continue",
        "X-Unity-Version": "2018.4.11f1",
        "X-GA": "v1 1",
        "ReleaseVersion": "OB52",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=edata, headers=headers) as resp:
                return resp.status
    except Exception:
        return None


async def send_multiple_requests(uid: str, region: str, url: str, burst_count: int = 100, system: str = None):
    msg = create_protobuf_message(uid, region)
    enc_uid = encrypt_message(msg)
    tokens = load_tokens(region, system)

    if not tokens:
        return []

    tasks = [
        send_request(enc_uid, tokens[i % len(tokens)]["token"], url)
        for i in range(burst_count)
    ]
    return await asyncio.gather(*tasks, return_exceptions=True)


def create_protobuf(uid: str) -> bytes:
    msg = uid_generator_pb2.uid_generator()
    msg.krishna_ = int(uid)
    msg.teamXdarks = 1
    return msg.SerializeToString()


def enc(uid: str) -> str:
    return encrypt_message(create_protobuf(uid))


def make_request(encrypted_hex: str, region: str, token: str):
    s = (region or "").upper()

    if s == "IND":
        url = "https://client.ind.freefiremobile.com/GetPlayerPersonalShow"
    elif s in {"BR", "US", "SAC", "NA"}:
        url = "https://client.us.freefiremobile.com/GetPlayerPersonalShow"
    else:
        url = "https://clientbp.ggblueshark.com/GetPlayerPersonalShow"

    edata = bytes.fromhex(encrypted_hex)
    headers = {
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
        "Connection": "Keep-Alive",
        "Accept-Encoding": "gzip",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/x-www-form-urlencoded",
        "Expect": "100-continue",
        "X-Unity-Version": "2018.4.11f1",
        "X-GA": "v1 1",
        "ReleaseVersion": "OB52",
    }

    try:
        resp = requests.post(url, data=edata, headers=headers, verify=False, timeout=30)
    except Exception:
        return None

    try:
        obj = like_count_pb2.Info()
        obj.ParseFromString(resp.content)
        return obj
    except Exception:
        return None


def _parse_account_info(pb_obj):
    try:
        if pb_obj is None:
            return None

        js = MessageToJson(pb_obj)
        data = json.loads(js)
        ai = data.get("AccountInfo", {})

        uid = int(ai.get("UID", 0))
        likes = int(ai.get("Likes", 0))
        name = str(ai.get("PlayerNickname", ""))

        if uid <= 0:
            return None

        return {"uid": uid, "likes": likes, "name": name}
    except Exception:
        return None


# ---------- Public like endpoint with required API key ----------
@app.get("/like")
@app.get("/like/<uid_path>")
def handle_like(uid_path=None):
    try:
        # API KEY CHECK
        key = (request.args.get("key") or "").strip()
        if key != API_KEY:
            return jsonify({
                "error": "invalid_api_key",
                "message": "Valid key required",
                "required_format": "/like?uid=<UID>&region=<REGION>&key=AX2"
            }), 403

        # uid: path param first, then query
        uid = (uid_path or request.args.get("uid") or "").strip()

        # region: region or server_name
        region = (request.args.get("region") or request.args.get("server_name") or "").strip().lower()

        # system
        system = (request.args.get("system") or "").strip()

        if system == "1":
            default_burst = 100
        elif system == "2":
            default_burst = 220
        else:
            default_burst = 220

        try:
            burst = int(request.args.get("burst", default_burst))
            burst = max(1, min(burst, 220))
        except Exception:
            burst = default_burst

        if not uid or not region:
            return jsonify({"error": "UID and region (or server_name) are required"}), 400

        if not str(uid).isdigit():
            return jsonify({"error": "UID must be numeric"}), 400

        # use system-aware tokens
        tokens = load_tokens(region, system)
        if not tokens or "token" not in tokens[0]:
            return jsonify({"error": "No valid tokens found for selected region/system"}), 500

        token = tokens[0]["token"]
        encrypted = enc(uid)

        before_obj = make_request(encrypted, region, token)
        before = _parse_account_info(before_obj)

        if before is None:
            return jsonify({
                "LikesGivenByAPI": 0,
                "LikesafterCommand": 0,
                "LikesbeforeCommand": 0,
                "PlayerNickname": "Unknown",
                "UID": int(uid),
                "status": 0
            }), 200

        s = region.upper()
        if s == "IND":
            url = "https://client.ind.freefiremobile.com/LikeProfile"
        elif s in {"BR", "US", "SAC", "NA"}:
            url = "https://client.us.freefiremobile.com/LikeProfile"
        else:
            url = "https://clientbp.ggblueshark.com/LikeProfile"

        asyncio.run(send_multiple_requests(uid, region, url, burst_count=burst, system=system))

        after_obj = make_request(encrypted, region, token)
        after = _parse_account_info(after_obj)

        if after is None:
            after = {
                "uid": before["uid"],
                "likes": before["likes"],
                "name": before["name"]
            }

        like_given = max(0, int(after["likes"]) - int(before["likes"]))
        status_value = 1 if like_given > 0 else 2

        return jsonify({
            "LikesGivenByAPI": like_given,
            "LikesafterCommand": int(after["likes"]),
            "LikesbeforeCommand": int(before["likes"]),
            "PlayerNickname": str(after["name"]),
            "UID": int(after["uid"]),
            "status": status_value
        }), 200

    except Exception as e:
        return jsonify({
            "error": "runtime_error",
            "detail": str(e)
        }), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)


