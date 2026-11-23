"""Dependency-free XLSX with inline text cells (no executable formulas)."""
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
from xml.sax.saxutils import escape
import json


def workbook(result):
    def column(index):
        name=""
        while index:
            index,remainder=divmod(index-1,26);name=chr(65+remainder)+name
        return name
    def sheet(rows):
        values=[]
        for number,row in enumerate(rows,1):
            cells=[]
            for index,value in enumerate(row,1):
                ref=column(index)+str(number)
                if value is None: cells.append(f'<c r="{ref}"/>')
                elif type(value) in {int,float}: cells.append(f'<c r="{ref}" t="n"><v>{value}</v></c>')
                elif type(value) is bool: cells.append(f'<c r="{ref}" t="b"><v>{int(value)}</v></c>')
                else: cells.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>')
            values.append(f'<row r="{number}">'+''.join(cells)+'</row>')
        return '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+''.join(values)+'</sheetData></worksheet>'
    buffer=BytesIO()
    with ZipFile(buffer,'w',ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml','''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>''')
        archive.writestr('_rels/.rels','''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>''')
        archive.writestr('xl/workbook.xml','''<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="分析结果" sheetId="1" r:id="rId1"/><sheet name="来源记录" sheetId="2" r:id="rId2"/></sheets></workbook>''')
        archive.writestr('xl/_rels/workbook.xml.rels','''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/></Relationships>''')
        archive.writestr('xl/worksheets/sheet1.xml',sheet([result['columns'],*result['rows']]))
        archive.writestr('xl/worksheets/sheet2.xml',sheet([['属性','值'],['查询编号',result['id']],['执行时间',result['executed_at']],['已截断',result['truncated']],['追踪编号',result.get('trace_id','')],['数据批次',json.dumps(result['data_batches'],ensure_ascii=False)]]))
    return buffer.getvalue()
