"""Generate only this independent application's local secrets; never print them."""
from pathlib import Path
import secrets,base64,json

root=Path(__file__).resolve().parent.parent
local=root/"local";local.mkdir(exist_ok=True)
(root/"evidence").mkdir(exist_ok=True)
path=local/"credentials.json"
if not path.exists():
    values={"database_password":secrets.token_hex(24),"encryption_key":base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),"admin_username":"admin","admin_password":"Wb!"+secrets.token_urlsafe(14),"warehouse_read_password":secrets.token_hex(20),"warehouse_write_password":secrets.token_hex(20)}
    path.write_text(json.dumps(values,indent=2),encoding="utf-8")
values=json.loads(path.read_text(encoding="utf-8"))
env=root/".env"
if not env.exists():env.write_text("DATABASE_PASSWORD="+values["database_password"]+"\nENCRYPTION_KEY="+values["encryption_key"]+"\nINITIAL_ADMIN_PASSWORD="+values["admin_password"]+"\n",encoding="utf-8")
(local/"本机登录信息.txt").write_text("独立数据工作台\n地址：http://127.0.0.1:19090\n账号：admin\n密码："+values["admin_password"]+"\n\n与 qData 的账号和数据库相互独立。\n",encoding="utf-8")
print("Local independent configuration prepared; no secrets printed.")
