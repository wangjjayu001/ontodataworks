"""Maintain reviewed Chinese glossary as versioned API metadata without changing schema fingerprints.
The explicit glossary is authoritative for this seed; reruns preserve subsequent steward edits.
"""
from runtime import *
from datetime import datetime
import hashlib

LABELS={
 'acquisition_channel':'获客渠道','active_customers':'去重买家数','approved_at':'审核通过时间',
 'average_order_value':'平均客单价','average_review_score':'平均评价分','business_date':'业务日期',
 'carrier':'承运商','category_name':'商品类目','city':'城市','converted':'会话转化标记',
 'customer_id':'客户账户编号','customer_unique_id':'跨账户买家标识','delivered_at':'实际送达时间',
 'delivery_delay_days':'预计与实际送达差额天数','discount_amount':'促销优惠金额','discount_rate':'活动折扣比例',
 'email':'客户邮箱','end_date':'活动结束日期','estimated_delivery_at':'预计送达时间',
 'event_at':'事件发生时间','event_name':'事件名称','event_no':'会话内事件序号',
 'freight_total':'订单运费合计','freight_value':'明细运费','gross_order_value':'含运费订单总额',
 'inventory_status':'库存风险状态','is_active':'商品可售标记','item_count':'订单明细行数',
 'items_total':'商品金额合计','list_price':'商品挂牌价','listed_date':'商品上架日期',
 'marketing_opt_in':'营销触达同意标记','on_hand_qty':'在库数量','onboarded_date':'商家入驻日期',
 'order_count':'订单数','order_id':'订单编号','order_item_id':'订单内明细序号','order_status':'订单状态',
 'package_weight_kg':'包装重量','page_views':'会话浏览页数','paid_at':'支付发生时间',
 'payment_amount':'支付流水金额','payment_count':'支付流水数','payment_id':'支付流水编号',
 'payment_method':'支付方式','payment_status':'支付状态','payment_total':'订单支付合计',
 'postal_code':'邮政编码','product_id':'商品编号','product_name':'商品名称','promotion_id':'促销活动编号',
 'promotion_name':'促销活动名称','promotion_status':'促销活动状态','promotion_type':'促销类型',
 'purchased_at':'下单时间','refund_amount':'退款金额','registered_at':'客户注册时间',
 'requested_at':'退货申请时间','reserved_qty':'预留数量','return_id':'退货申请编号',
 'return_reason':'退货原因','return_status':'退货状态','review_id':'评价编号',
 'review_score':'订单评价分','review_topic':'评价主题','reviewed_at':'评价创建时间',
 'sales_channel':'销售渠道','seller_id':'商家编号','seller_state':'商家省级地区',
 'seller_status':'商家经营状态','service_level':'物流服务等级','session_id':'访问会话编号',
 'shipment_id':'运单编号','shipment_status':'物流状态','shipped_at':'发货时间',
 'snapshot_date':'库存快照日期','start_date':'活动开始日期','started_at':'会话开始时间',
 'state':'省级地区代码','unit_price':'商品成交单价',
 'id':'验收记录编号','note':'验收说明','day_key':'自然日','year_no':'年份','month_no':'月份',
 'reading_id':'读数记录编号','sample_group':'样本分组','reading_value':'人工读数值',
 'reading_count':'读数记录数','reading_sum':'读数合计'}

TEAM={'订单交易':'交易数据维护组','客户':'客户数据维护组','商品目录':'商品数据维护组','商家':'商家数据维护组',
 '支付结算':'支付结算维护组','售后评价':'售后数据维护组','履约物流':'履约数据维护组','库存供应链':'库存数据维护组',
 '营销促销':'营销数据维护组','客户行为':'用户行为维护组','经营分析':'经营分析维护组'}
GRAIN={'customers':'一个客户账户，按客户账户编号唯一；同一买家可以关联多个账户',
 'products':'一个商品，按商品编号唯一','sellers':'一个商家，按商家编号唯一','promotions':'一个促销活动，按活动编号唯一',
 'orders':'一个订单，按订单编号唯一','order_items':'一个订单商品行，按订单编号与订单内明细序号组合唯一；当前每行一件商品',
 'order_promotions':'一个订单与活动的关联，按订单编号与活动编号组合唯一','payments':'一笔支付流水，按支付流水编号唯一',
 'shipments':'一个物流运单，按运单编号唯一','returns':'一笔退货申请，按退货申请编号唯一','reviews':'一条订单评价，按评价编号唯一',
 'inventory_snapshots':'一个商品在一个自然日的库存快照，按商品编号与快照日期组合唯一',
 'inventory_daily':'一个商品在一个自然日的库存事实，按商品编号与快照日期组合唯一',
 'sessions':'一次访问会话，按会话编号唯一','session_events':'一次会话中的一个事件，按会话编号与事件序号组合唯一',
 'order_totals':'一个订单的商品金额与运费汇总，按订单编号唯一','order_finance':'一个订单的支付汇总，按订单编号唯一',
 'daily_kpis':'一个下单自然日的经营指标汇总，按业务日期唯一；日去重买家数不可跨日直接累加'}
FIXTURES={
 'ads_validation_probe':('验收探针记录表','一条独立工程验收记录，按验收记录编号唯一'),
 'dim_validation_day':('工程验收日期维度表','一个自然日，按自然日唯一'),
 'dwd_validation_reading':('工程验收人工读数明细表','一个自然日与样本组的一条读数，按读数记录编号唯一'),
 'dws_validation_daily':('工程验收读数日汇总表','一个自然日的读数汇总，按自然日唯一')}
FIXTURE_DEFINITIONS={'id':'工程验收记录的唯一编号，不对应真实业务实体','note':'工程验收说明文本，不包含真实业务信息',
 'day_key':'验收数据对应的自然日，日期范围为2025-01-01至2026-12-31','year_no':'自然日所属公历年份',
 'month_no':'自然日所属月份，范围1至12','reading_id':'人工读数记录的唯一编号',
 'sample_group':'确定性工程样本分组，A或B，不对应电商业务人群','reading_value':'确定性公式生成的人工读数，仅用于查询和权限验收',
 'reading_count':'当日人工读数记录数量','reading_sum':'当日人工读数值的合计'}
MONEY={'average_order_value','discount_amount','freight_total','freight_value','gross_order_value','items_total','list_price','payment_amount','payment_total','refund_amount','unit_price'}
ENUMS={'acquisition_channel','carrier','category_name','event_name','inventory_status','order_status','payment_method','payment_status','promotion_status','promotion_type','return_reason','return_status','review_topic','sales_channel','seller_status','service_level','shipment_status','state','seller_state','sample_group'}

def write_json(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
def has_chinese(text):return any('\u4e00'<=char<='\u9fff' for char in text)
def quote(text):return '"'+text.replace('"','""')+'"'

def main():
    cr=json.loads((ROOT/'local/credentials.json').read_text('utf-8'));client=Client();client.login(cr['admin_username'],cr['admin_password'])
    state=json.loads((ROOT/'local/ecommerce_import/state.json').read_text('utf-8'))
    output=ROOT/'evidence/ecommerce';output.mkdir(exist_ok=True)
    report={'checked_at':datetime.now().isoformat(),'tables':[],'source':'explicit Chinese glossary + current physical metadata + documented source anomalies'}
    for ws in client.call('/auth/me')['workspaces']:
        b='/workspaces/'+str(ws['id']);domains={d['id']:d for d in client.call(b+'/documents/domain')}
        sources={s['id']:s for s in client.call(b+'/datasources')};assets=client.call(b+'/assets')
        for asset in assets:
            src=sources[asset['datasource_id']];ecommerce=src['database']=='ecommerce_validation'
            if not ecommerce and asset['table_name'] not in FIXTURES:continue
            domain_name=domains.get(asset['domain_id'],{}).get('name')
            if not ecommerce:
                fixture_domain=next((d for d in domains.values() if '验收' in d['name']),None)
                assert fixture_domain
                label,grain=FIXTURES[asset['table_name']]
                client.call(b+'/assets/'+str(asset['id']),'PATCH',{'name':label,'domain_id':fixture_domain['id'],
                    'description':label+'；'+grain+'。此为平台功能验收数据，非电商数仓。'})
                domain_name=fixture_domain['name'];owner='本地工程验收维护组'
            else:
                original=asset['table_name'][len(asset['namespace'])+1:]
                short=original[4:] if original.startswith('fct_') else original
                grain=GRAIN[short];owner=TEAM[domain_name]+'（本地开发者代管）'
            keys=[c['name'] for c in asset['columns'] if c.get('primary_key')]
            quality='确定性人工模拟数据，用于本地功能验证，不是生产认证。'
            if ecommerce:
                quality+='快照截止2026-10-26。原数据存在负支付110笔、活动期外关联12197条、上架前交易17545行、入驻前交易459行、转化矛盾90000会话，以及库存预留超量等已记录异常；参见电商数仓迁移验收报告。'
                if asset['namespace']=='dim':quality+='当前维度为Type1快照，未实现SCD2历史。'
                if any(c['name'] in MONEY and c['type']=='double precision' for c in asset['columns']):quality+='金额含DOUBLE字段，未完成精确财务存储改造。'
            else:quality+='工程验收范围为2025—2026年，原始读数为人工公式值。'
            columns=[]
            for col in asset['columns']:
                name=col['name'];assert name in LABELS,(name,asset['table_name'])
                definition=col['comment'] if ecommerce else FIXTURE_DEFINITIONS[name]
                if name=='customer_id':definition='客户账户的唯一编号；一个跨账户买家可对应多个客户账户，账户数与买家数不可混用。'
                elif name=='customer_unique_id':definition='用于跨客户账户去重的买家标识；全期间当前样本含16000个不同买家，对应20000个客户账户。'
                elif name=='active_customers':definition='当日按customer_unique_id去重的买家数；期间买家数须在订单明细重新去重，不得直接累加每日值。'
                elif name=='delivery_delay_days':definition='预计送达时间减实际送达时间的小时边界差，除以24并保留两位；正值表示提前、负值表示迟到。'
                elif name=='refund_amount' and asset['namespace']=='dwd' and original=='fct_orders':definition='该订单退货申请退款金额的合计；仅代表申请退款口径，未证明资金已实际退回。'
                elif name=='refund_amount' and asset['namespace']=='dws':definition='当日下单订单所关联的申请退款金额合计，以订单下单日归属；不是退款申请日或到账日口径。'
                elif name=='refund_amount':definition='退货申请中的退款金额；未证明资金已实际退回。'
                if name in MONEY:definition+=' 金额币种未在源规范声明，不能直接认定为人民币。'
                assert has_chinese(definition),(asset['table_name'],name,definition)
                if col.get('primary_key') or col.get('foreign_keys') or name=='customer_unique_id':role='identifier'
                elif 'timestamp' in col['type'] or col['type']=='date':role='event_time'
                elif name.endswith('_status') or col['type']=='boolean':role='status'
                elif name in MONEY or col['type'] in {'bigint','integer','double precision'} or col['type'].startswith('numeric'):role='measure'
                else:role='attribute'
                if name in {'postal_code','year_no','month_no'}:role='attribute'
                rules=['物理字段'+('允许空值，空值语义以业务说明为准' if col['nullable'] else '不允许空值')]
                if col.get('primary_key'):rules.append('参与物理主键（'+','.join(keys)+'）；主键组合非空且唯一，不要求单个复合键字段独自唯一')
                for fk in col.get('foreign_keys',[]):rules.append('外键引用 '+fk['namespace']+'.'+fk['table']+'.'+fk['column']+'，由数据库约束校验')
                if name in MONEY:rules.append('正向金额的非负性属于业务质量门禁；原模拟数据的已知异常仍保留，不代表本规则全部通过')
                if name=='snapshot_date':rules.append('当前模拟库存快照2025-01-01至2026-10-26连续；按指定日取值，不跨日相加库存')
                if name=='review_score':rules.append('有效评价分应在1至5范围；空值表示无评价')
                if name=='discount_rate':rules.append('折扣比例应在0至1之间；解释折扣额时须结合促销类型')
                if role=='event_time' and 'timestamp' in col['type']:rules.append('数据库类型为无时区timestamp；业务约定Asia/Shanghai，需在跨时区查询时显式转换')
                enum=[]
                if name in ENUMS:
                    q='SELECT '+quote(name)+' FROM '+quote(asset['namespace'])+'.'+quote(asset['table_name'])+' WHERE '+quote(name)+' IS NOT NULL GROUP BY 1 ORDER BY 1 LIMIT 101'
                    result=client.call(b+'/queries','POST',{'datasource_id':asset['datasource_id'],'sql':q})
                    if len(result['rows'])<=100:enum=[str(row[0]) for row in result['rows']]
                unit='金额单位（币种未定义）' if name in MONEY else '千克' if name=='package_weight_kg' else '天' if name=='delivery_delay_days' else '分' if name in {'review_score','average_review_score'} else '件' if name in {'on_hand_qty','reserved_qty'} else '次' if name in {'page_views','payment_count','order_count','reading_count'} else ''
                columns.append({'name':name,'chinese_name':LABELS[name],'definition':definition,'semantic_role':role,
                    'classification':'sensitive' if name in {'email','customer_unique_id'} else 'internal','unit':unit,'quality_rules':rules,'enum_values':enum})
            spec={'owner':owner,'grain':'每行表示'+grain,'update_policy':
                '静态验证快照，按受控脚本重建并全量核对；未接入日调度或增量更新' if ecommerce else '确定性工程验收脚本生成；按需重新执行，不自动采集业务数据',
                'quality_note':quality,'certification':'validation_only','columns':columns}
            old=asset.get('governance')
            if old:
                # Steward changes win. The seed never overwrites later versions on rerun.
                assert set(c['name'] for c in old['spec']['columns'])==set(c['name'] for c in columns),'Schema changed; review required'
                record=old
            else:record=client.call(b+'/assets/'+str(asset['id'])+'/metadata','PUT',{'revision':0,**spec})
            report['tables'].append({'workspace_id':ws['id'],'workspace':ws['name'],'asset_id':asset['id'],
                'table':asset['namespace']+'.'+asset['table_name'],'columns':len(columns),'revision':record['revision'],'metadata':record['spec']})
        print('MAINTAINED',ws['name'],sum(t['workspace_id']==ws['id'] for t in report['tables']),'assets',flush=True)
    for ws in client.call('/auth/me')['workspaces']:
        for asset in client.call('/workspaces/'+str(ws['id'])+'/assets'):
            if any(t['asset_id']==asset['id'] for t in report['tables']):assert asset['metadata_state']=='complete'
    report['summary']={'assets':len(report['tables']),'columns':sum(t['columns'] for t in report['tables']),
        'ecommerce_assets':sum(t['workspace_id']==state['workspace_id'] for t in report['tables']),
        'ecommerce_columns':sum(t['columns'] for t in report['tables'] if t['workspace_id']==state['workspace_id'])}
    write_json(output/'chinese_metadata_maintenance.json',report)
    print(json.dumps(report['summary'],ensure_ascii=False),flush=True)

if __name__=='__main__':main()
