from pathlib import Path
import json,subprocess,urllib.request,urllib.error,http.cookiejar

ROOT=Path(__file__).resolve().parent.parent
CLI=ROOT.parent/"runtime/DockerDesktop/resources/bin/docker.exe"
ARGS=[str(CLI),"compose","--project-directory",str(ROOT),"--env-file",str(ROOT/".env"),"-f",str(ROOT/"compose.yml")]


def compose(*args,stdin=None):
    p=subprocess.run(ARGS+list(args),input=stdin,capture_output=True,text=True,encoding="utf-8",errors="replace")
    if p.returncode:raise RuntimeError(f"Compose {args[:2]} failed: {p.stderr[-1500:]}")
    return p.stdout.strip()


def sql(statement,database="workbench"):
    return compose("exec","-T","db","psql","-U","workbench","-d",database,"-v","ON_ERROR_STOP=1","-A","-t",stdin=statement)


class Client:
    def __init__(self):
        self.jar=http.cookiejar.CookieJar()
        self.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf=""

    def call(self,path,method="GET",body=None,expected=200):
        headers={"Content-Type":"application/json","Origin":"http://127.0.0.1:19090"}
        if self.csrf:headers["X-CSRF-Token"]=self.csrf
        data=json.dumps(body).encode() if body is not None else None
        try:
            response=self.opener.open(urllib.request.Request("http://127.0.0.1:19090/api"+path,data=data,headers=headers,method=method),timeout=35)
        except urllib.error.HTTPError as exc:
            response=exc
        status=response.code
        payload=json.load(response)
        if status!=expected:raise AssertionError(f"{method} {path} returned {status}, expected {expected}: {payload}")
        return payload

    def login(self,username,password):
        data=self.call("/auth/login","POST",{"username":username,"password":password})
        self.csrf=data["csrf"]
        return data
