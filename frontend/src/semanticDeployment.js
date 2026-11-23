const scopes=new Set(['local_validation','production']);
const unwrap=value=>value?.data??value;

function assertScope(scope){if(!scopes.has(scope))throw new Error('未知部署环境，已停止激活。');}

export async function readSemanticDeployment(request,scope){
 assertScope(scope);
 const value=unwrap(await request('/semantic/deployments/'+scope));
 if(!value||value.scope!==scope||!Object.hasOwn(value,'deployment'))throw new Error('部署环境响应不匹配，已停止激活。');
 const deployment=value.deployment;
 if(deployment!==null&&(!deployment||deployment.scope!==scope||!Number.isSafeInteger(deployment.pointer_version)||deployment.pointer_version<1))throw new Error('部署指针缺失或环境不匹配，已停止激活。');
 return {scope,deployment,pointerVersion:deployment===null?0:deployment.pointer_version};
}

export function semanticActivationCommand(scope,current,item,index,reason){
 assertScope(scope);
 if(current?.scope!==scope||item?.scope!==scope)throw new Error('发布与部署环境不一致，已停止激活。');
 const expected=current.deployment===null?0:current.deployment?.pointer_version;
 if(!Number.isSafeInteger(expected)||expected<0||expected!==current.pointerVersion||current.deployment&&current.deployment.scope!==scope)throw new Error('部署指针无效，请重新读取当前环境。');
 const release=item.release_ref,snapshot=index?.index_snapshot_ref,content=index?.index_snapshot;
 if(!release?.id||!release.content_hash||!snapshot?.id||!snapshot.content_hash||content?.id!==snapshot.id||content.release_id!==release.id||content.release_hash!==release.content_hash)throw new Error('索引与发布不属于同一个完整快照，已停止激活。');
 if(typeof reason!=='string'||!reason.trim())throw new Error('请填写本次激活或回滚的理由。');
 return {path:`/deployments/${scope}/activate`,version:expected,body:{release_ref:release,index_snapshot_ref:snapshot,expected_pointer_version:expected,reason:reason.trim()}};
}
