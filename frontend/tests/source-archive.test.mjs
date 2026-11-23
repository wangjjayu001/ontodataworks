import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash,webcrypto} from 'node:crypto';
import {decodeSourceFile,inspectSourceText,SOURCE_MAX_BYTES} from '../src/sourceArchive.js';

const hash=text=>createHash('sha256').update(text,'utf8').digest('hex');
test('UTF-8 source retains Unicode and exact CRLF bytes and matches independent hash',async()=>{
 const raw='{\r\n  "业务定义": "按订单编号去重 🧾",\r\n  "count": 1\r\n}\r\n';
 const bytes=new TextEncoder().encode(raw);
 const decoded=decodeSourceFile(bytes.buffer);
 const receipt=await inspectSourceText(decoded,hash(raw),webcrypto);
 assert.equal(decoded,raw);
 assert.equal(receipt.byteLength,Buffer.byteLength(raw,'utf8'));
 assert.equal(receipt.contentHash,hash(raw));
 assert.equal(receipt.matchesExpected,true);
});
test('equivalent JSON with different whitespace never matches original source revision',async()=>{
 const original='{\r\n"a":1\r\n}',changed='{\n"a":1\n}';
 const receipt=await inspectSourceText(changed,hash(original),webcrypto);
 assert.equal(receipt.matchesExpected,false);
 assert.notEqual(receipt.contentHash,hash(original));
});
test('malformed, empty and oversized source are rejected before archive',async()=>{
 await assert.rejects(inspectSourceText('{"a":',null,webcrypto),/合法 JSON/);
 await assert.rejects(inspectSourceText('  ',null,webcrypto),/请选择文件/);
 await assert.rejects(inspectSourceText('"'+'a'.repeat(SOURCE_MAX_BYTES)+'"',null,webcrypto),/超过 2 MB/);
});
test('BOM and invalid UTF-8 cannot be silently normalized into accepted source',async()=>{
 const bomBytes=Uint8Array.from([0xef,0xbb,0xbf,0x7b,0x7d]);
 const decoded=decodeSourceFile(bomBytes.buffer);
 assert.equal(decoded.charCodeAt(0),0xfeff);
 await assert.rejects(inspectSourceText(decoded,hash('{}'),webcrypto),/BOM/);
 assert.throws(()=>decodeSourceFile(Uint8Array.from([0xc3,0x28]).buffer),/不是合法 UTF-8/);
});
test('hash mismatch is explicit and no expected hash is invented',async()=>{
 const receipt=await inspectSourceText('{"actual":true}',null,webcrypto);
 assert.equal(receipt.matchesExpected,false);
 assert.match(receipt.contentHash,/^[a-f0-9]{64}$/);
});
