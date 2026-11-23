export const SOURCE_MAX_BYTES=2_000_000;

export async function inspectSourceText(rawText,expectedHash,cryptoProvider=globalThis.crypto){
 if(typeof rawText!=='string')throw new Error('来源原文必须是 JSON 文本。');
 const bytes=new TextEncoder().encode(rawText);
 if(bytes.byteLength>SOURCE_MAX_BYTES)throw new Error('来源原文超过 2 MB，请使用与该来源修订一致的较小 JSON。');
 if(!rawText.trim())throw new Error('请选择文件或粘贴来源原文。');
 if(rawText.charCodeAt(0)===0xFEFF)throw new Error('文件含 UTF-8 BOM；当前归档仅接受无 BOM 的原始 JSON，请维护对应来源修订后再归档。');
 try{JSON.parse(rawText);}catch{throw new Error('原文不是合法 JSON；请保留原始内容并核对文件格式。');}
 if(!cryptoProvider?.subtle)throw new Error('当前浏览器无法计算来源哈希，请通过本机地址或安全连接访问。');
 const digest=await cryptoProvider.subtle.digest('SHA-256',bytes);
 const contentHash=Array.from(new Uint8Array(digest),v=>v.toString(16).padStart(2,'0')).join('');
 return {rawText,byteLength:bytes.byteLength,contentHash,matchesExpected:typeof expectedHash==='string'&&contentHash===expectedHash};
}

export function decodeSourceFile(buffer){
 if(buffer.byteLength>SOURCE_MAX_BYTES)throw new Error('来源文件超过 2 MB。');
 try{return new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(buffer);}catch{throw new Error('来源文件不是合法 UTF-8，请保留原文并核对编码。');}
}
