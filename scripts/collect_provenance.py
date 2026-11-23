"""Record newly implemented sources and installed third-party notices without secrets."""
from runtime import *
from datetime import datetime
import hashlib,re

target=ROOT/"third-party-licenses";target.mkdir(exist_ok=True)
entries=[]
modules=ROOT/"frontend/node_modules"
packages=list(modules.glob("*/package.json"))+list(modules.glob("@*/*/package.json"))
for manifest in packages:
    info=json.loads(manifest.read_text(encoding="utf-8"));name=info.get("name",manifest.parent.name)
    out=target/("npm-"+name.replace("/","_").replace("@",""));out.mkdir(exist_ok=True)
    texts=[]
    for file in manifest.parent.iterdir():
        if file.is_file() and re.match(r"^(LICEN[SC]E|COPYING|NOTICE)",file.name,re.I):
            (out/file.name).write_bytes(file.read_bytes());texts.append(str((out/file.name).relative_to(ROOT)))
    entries.append({"ecosystem":"npm","name":name,"version":info.get("version"),"license":info.get("license","See package notices"),"notices":texts})

code=r'''
import importlib.metadata as m,json
rows=[]
for d in m.distributions():
 files=[]
 for f in d.files or []:
  parts=str(f).lower()
  if ('.dist-info/' in parts and any(x in parts for x in ['/license','/notice','/copying'])):
   p=d.locate_file(f)
   if p.is_file():files.append({'name':str(f).split('.dist-info/',1)[1].replace('/','_'),'text':p.read_text(encoding='utf-8',errors='replace')})
 rows.append({'name':d.metadata['Name'],'version':d.version,'license':d.metadata.get('License-Expression') or d.metadata.get('License') or 'See distribution license files','files':files})
print(json.dumps(rows))
'''
backend=json.loads(compose("exec","-T","api","python","-",stdin=code))
for item in backend:
    out=target/("python-"+item["name"]);out.mkdir(exist_ok=True)
    notices=[]
    for file in item.pop("files"):
        path=out/file["name"];path.write_text(file["text"],encoding="utf-8");notices.append(str(path.relative_to(ROOT)))
    entries.append({"ecosystem":"python",**item,"notices":notices})

sources=[]
for sub in ["backend/app","frontend/src","frontend/public","infra","scripts"]:
    for path in (ROOT/sub).rglob("*"):
        if path.is_file() and "__pycache__" not in str(path):sources.append({"file":path.relative_to(ROOT).as_posix(),"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"origin":"Written independently in this workspace; not copied from qData"})
report={"checked_at":datetime.now().isoformat(),"business_code_origin":"Independent implementation from an empty directory", "qdata_runtime_dependency":False,"qdata_files_imported":[],"third_party_dependencies":entries,"original_source_files":sources,"scope":"Source and installed package inventory, not a claim that third-party software is license-free"}
(ROOT/"evidence/source-and-dependency-inventory.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
lines=["# 第三方组件与许可保留", "", "原创业务实现与第三方组件分别维护。以下记录实际安装的前端构建依赖及后端依赖，不将其版权替换为本项目版权。完整许可文本保存在 third-party-licenses；运行容器也保留各发行包本身的许可文件。", "", "| 生态 | 软件包 | 版本 | 声明许可 |", "| --- | --- | --- | --- |"]
for e in entries:
    license_text=str(e["license"]).replace("\n"," ").replace("|","/")
    if len(license_text)>100:license_text="完整声明见对应许可文件"
    lines.append(f"| {e['ecosystem']} | {e['name']} | {e['version']} | {license_text} |")
lines += ["", "PostgreSQL 16 使用 PostgreSQL License；Nginx 使用 BSD 类许可；Python 运行时、Debian/Alpine 系统及 psycopg-binary 内含原生库另有各自声明，分发镜像时应一并保留其发行版中的版权文件。前述包的清单不替代容器操作系统的完整 SBOM。", "", "镜像 digest 固定于 compose.yml 与 backend/Dockerfile，容器实际镜像 ID 记录在持久化验收文件。", "", "没有对 qData 的 LICENSE/NOTICE 做重写、去重或替换；qData 安装目录属于保留的旧环境。本项目不导入或运行其业务代码。"]
(ROOT/"THIRD_PARTY_NOTICES.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
print(json.dumps({"source_files":len(sources),"third_party_packages":len(entries),"qdata_runtime_dependency":False}))
