# -*- coding: utf-8 -*-
"""license_keygen —— 生成激活体系 Ed25519 密钥对（R2/R3）

用法：
    .venv/bin/python tools/license_keygen.py --priv data/license_dev_private.pem

- 私钥：只在签名方保存（R3 部署时在 ECS 上生成并存 /etc/wrongbook-license/，0600）
- 公钥：替换 license_client.py 里的 LICENSE_PUBKEY_PEM 常量（客户端只嵌公钥）
- 开发期密钥对仅用于本地 mock 服务器联调，**发布前必须换正式密钥**
"""
import argparse, os, base64
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--priv", default="data/license_dev_private.pem",
                    help="私钥输出路径（默认 data/ 下，已被 gitignore）")
    args = ap.parse_args()

    key = Ed25519PrivateKey.generate()
    priv_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption())
    pub_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo)

    os.makedirs(os.path.dirname(os.path.abspath(args.priv)), exist_ok=True)
    with open(args.priv, "wb") as f:
        f.write(priv_pem)
    os.chmod(args.priv, 0o600)

    print("私钥 →", args.priv, "（0600，勿外传/勿入库）")
    print("公钥 PEM（替换 license_client.LICENSE_PUBKEY_PEM）:")
    print(pub_pem.decode())
    print("公钥 base64 单行（嵌入常量用）:")
    print(base64.b64encode(pub_pem).decode())


if __name__ == "__main__":
    main()
