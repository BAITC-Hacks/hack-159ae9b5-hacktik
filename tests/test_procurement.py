import calendar
import csv
import hashlib
import io
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.forecast import calculate, calculate_product, history_profile
from app.main import create_app
from app.procurement_db import Database
from app.procurement_models import Scenario
from app.router import DemoRouter


def csv_text(rows):
    f = io.StringIO()
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader(); w.writerows(rows)
    return f.getvalue()


def product(**updates):
    return {"product_id": "TEST:1", "warehouse": "A", "name": "Тестовый товар", "unit": "шт", "category": "C",
            "stock_qty": 10, "reserved_qty": 0, "stock_basis": "physical_snapshot", "stock_date": "2026-09-23",
            "forecast_30_days": 100, "forecast_90_days": 300, "moq": 1, "moq_rule": "minimum", **updates}


def monthly(years=2, seasonal=False, growth=False, available=None):
    rows=[]
    for i in range(12*years):
        year=2024+i//12;month=i%12+1
        days=calendar.monthrange(year,month)[1]
        rate=(10 if month in (6,7,8) and seasonal else 2) * (1+i*.025 if growth else 1)
        rows.append({"product_id":"TEST:1", "warehouse":"A", "month":f"{year}-{month:02}",
                     "quantity":rate*days, **({"available_days":available} if available else {})})
    return rows


@pytest.fixture
def db(tmp_path):
    d=Database(tmp_path/'test.sqlite3',seed=False)
    d.import_csv('snapshot',csv_text([product()]))
    return d


@pytest.fixture
def client(db):
    return TestClient(create_app(router=DemoRouter(),procurement_db=db))


def test_real_uploaded_csv_import_is_lossless(tmp_path):
    db=Database(tmp_path/'real.sqlite3')
    s=db.snapshot()
    assert len(s['products'])==1922
    assert sum(p['classification']=='deficit' for p in s['products'])==367
    assert sum(p['classification']=='surplus' for p in s['products'])==1555
    assert all(not p.get('supplier_id') for p in s['products'])
    assert all(p.get('unit_cost') in (None,'') for p in s['products'])
    assert len(Database(db.path).snapshot()['products'])==1922
    baseline=calculate(s,Scenario(as_of=date(2026,9,23)))
    for r in baseline:
        actual=r['quantity'] if r['source_classification']=='deficit' else r['surplus']
        assert actual==pytest.approx(r['source_quantity'],abs=.0011)


def test_every_source_changes_order_and_no_double_trend():
    p=product(trend_factor=1.25)
    sc=Scenario(as_of=date(2026,9,23))
    base=calculate_product(p,sc,{})
    assert base['quantity']==90  # Imported 100 forecast is not multiplied by 1.25 again.
    assert calculate_product({**p,'stock_qty':40},sc,{})['quantity']==60
    assert calculate_product(p,sc,{'inbound':[{'eta':'2026-10-01','quantity':40}]})['quantity']==50
    assert calculate_product(p,Scenario(as_of=sc.as_of,growth_pct=20),{})['quantity']==110
    assert calculate_product(p,sc,{},category={'growth_pct':20,'safety_days':0})['quantity']==110
    assert calculate_product(p,sc,{},supplier={'name':'S','lead_days':15})['quantity']==140
    assert calculate_product(p,sc,{},category={'safety_days':3})['quantity']==100
    assert calculate_product(p,sc,{'inbound':[{'eta':'2026-12-01','quantity':500}]})['quantity']==90


def test_free_stock_is_not_reduced_by_reserves_twice():
    r=calculate_product(product(stock_qty=20,reserved_qty=5,stock_basis='free_stock_snapshot'),Scenario(),{})
    assert r['free_stock']==20
    physical=calculate_product(product(stock_qty=20,reserved_qty=5),Scenario(),{})
    assert physical['free_stock']==15


def test_meter_quantities_keep_fractional_order_and_surplus():
    sc=Scenario(as_of=date(2026,9,23))
    p=product(unit='м',stock_qty=0,forecast_30_days=.509,forecast_90_days=1.6,moq='')
    r=calculate_product(p,sc,{})
    assert r['quantity']==.509
    r=calculate_product({**p,'stock_qty':2},sc,{})
    assert r['surplus']==.4


def test_seasonality_peaks_in_summer():
    rows=monthly(seasonal=True)
    summer=calculate_product(product(),Scenario(as_of=date(2026,7,1)),{'monthly_sales':rows})
    winter=calculate_product(product(),Scenario(as_of=date(2026,1,1)),{'monthly_sales':rows})
    assert summer['forecast_30'] > winter['forecast_30'] * 2.5
    assert summer['mode']=='history'


def test_sustained_growth_is_retained():
    p=product()
    flat=calculate_product(p,Scenario(as_of=date(2026,1,1)),{'monthly_sales':monthly()})
    growing=calculate_product(p,Scenario(as_of=date(2026,1,1)),{'monthly_sales':monthly(growth=True)})
    assert growing['forecast_30'] > flat['forecast_30']*1.4
    assert growing['trend']>1


def test_stockout_restores_lost_demand():
    rows=monthly(years=1)
    raw=history_profile([],rows,[],date(2025,1,1))
    periods=[{'start_date':'2024-12-01','end_date':'2024-12-15'}]
    corrected=history_profile([],rows,periods,date(2025,1,1))
    assert corrected['base_daily']>raw['base_daily']
    assert corrected['lost']>0
    full=history_profile([],rows,[{'start_date':'2024-12-01','end_date':'2024-12-31'}],date(2025,1,1))
    assert full['series'][-1]['rate']>0


def test_one_off_large_client_order_even_split_does_not_raise_forecast():
    rows=[]
    for i in range(180):
        d=date(2025,1,1)+timedelta(days=i)
        rows.append({'date':str(d),'quantity':5,'customer_hash':hashlib.sha256(str(i%10).encode()).hexdigest()})
    base=history_profile(rows,[],[],date(2025,7,1))
    big=[{'date':'2025-06-12','quantity':1000,'customer_hash':'f'*64} for _ in range(4)]
    adjusted=history_profile(rows+big,[],[],date(2025,7,1))
    assert adjusted['excluded']==4000
    assert adjusted['base_daily']==pytest.approx(base['base_daily'])


def test_monthly_totals_and_transaction_outliers_both_used():
    detail=[{'date':f'2025-06-{i:02}','quantity':5,'customer_hash':'a'*64} for i in range(1,20)]
    detail.append({'date':'2025-06-23','quantity':1000,'customer_hash':'b'*64})
    monthly=[{'month':'2025-06','quantity':1150}]
    p=history_profile(detail,monthly,[],date(2025,7,1))
    assert p['series'][0]['raw']==1150
    assert p['series'][0]['regular']==150
    assert p['excluded']==1000


def test_category_and_warehouse_filters(db):
    db.import_csv('snapshot',csv_text([product(warehouse='B',category='Other',stock_qty=99)]))
    rows=calculate(db.snapshot(),Scenario(warehouse='B',category='Other'))
    assert len(rows)==1 and rows[0]['quantity']==1


def test_import_atomicity_finite_numbers_privacy_and_reimport(db):
    with pytest.raises(ValueError):
        db.import_csv('snapshot',csv_text([product(product_id='NEW'),product(stock_qty='NaN')]))
    assert len(db.snapshot()['products'])==1
    with pytest.raises(ValueError,match='customer_hash'):
        db.import_csv('sales',csv_text([{'transaction_id':'T1','product_id':'TEST:1','warehouse':'A','date':'2025-01-01','quantity':1,'customer_hash':'ivan@example.com'}]))
    rows=[{'product_id':'TEST:1','warehouse':'A','eta':'2026-10-01','quantity':5,'reference':'PO-1'}]
    db.import_csv('inbound',csv_text(rows));db.import_csv('inbound',csv_text(rows))
    assert len(db.snapshot()['sources']['inbound'])==1


def setup_order(client,db,price=12):
    db.save_supplier({'supplier_id':'S1','name':'Поставщик','lead_days':0,'minimum_order_value':0})
    db.edit('TEST:1','A',{'supplier_id':'S1','unit_cost':price})
    scenario=Scenario(as_of=date(2026,9,23)).model_dump(mode='json')
    result=client.post('/api/procurement/orders',json={'scenario':scenario,'lines':[{'product_id':'TEST:1','warehouse':'A','quantity':90} ]})
    assert result.status_code==200, result.text
    return result.json()


def test_draft_approval_export_and_persistence(client,db):
    order=setup_order(client,db)
    assert order['status']=='draft'
    assert client.post(f"/api/procurement/orders/{order['id']}/approve",json={'reviewer':'Manager','confirmed':False}).status_code==422
    assert client.post(f"/api/procurement/orders/{order['id']}/approve",json={'reviewer':'Manager','confirmed':True}).status_code==422
    data={'reviewer':'Manager','confirmed':True,'acknowledge_data_gaps':True}
    approved=client.post(f"/api/procurement/orders/{order['id']}/approve",json=data)
    assert approved.status_code==200,approved.text
    assert approved.json()['status']=='approved'
    assert client.post(f"/api/procurement/orders/{order['id']}/approve",json=data).json()['approved_at']==approved.json()['approved_at']
    assert Database(db.path,seed=False).orders()[0]['reviewer']=='Manager'
    export=client.get(f"/api/procurement/orders/{order['id']}/export")
    assert export.content.startswith(b'\xef\xbb\xbf') and 'approved' in export.text


def test_changed_stock_blocks_stale_draft(client,db):
    order=setup_order(client,db)
    db.edit('TEST:1','A',{'stock_qty':20})
    r=client.post(f"/api/procurement/orders/{order['id']}/approve",json={'reviewer':'Manager','confirmed':True,'acknowledge_data_gaps':True})
    assert r.status_code==409


def test_missing_price_and_minimum_value_block_approval(client,db):
    order=setup_order(client,db,price=None)
    body={'reviewer':'Manager','confirmed':True,'acknowledge_data_gaps':True}
    assert client.post(f"/api/procurement/orders/{order['id']}/approve",json=body).status_code==422
    db.save_supplier({'supplier_id':'S1','name':'Поставщик','lead_days':0,'minimum_order_value':5000})
    db.edit('TEST:1','A',{'unit_cost':10})
    scenario=Scenario(as_of=date(2026,9,23)).model_dump(mode='json')
    o=client.post('/api/procurement/orders',json={'scenario':scenario,'lines':[{'product_id':'TEST:1','warehouse':'A','quantity':90}]}).json()
    assert client.post(f"/api/procurement/orders/{o['id']}/approve",json=body).status_code==422


def test_moq_manual_adjustment_needs_reason(client,db):
    setup_order(client,db)
    db.edit('TEST:1','A',{'moq':12,'moq_rule':'multiple'})
    scenario=Scenario(as_of=date(2026,9,23)).model_dump(mode='json')
    def create(q,reason=''):
        return client.post('/api/procurement/orders',json={'scenario':scenario,'lines':[{'product_id':'TEST:1','warehouse':'A','quantity':q,'reason':reason}]})
    assert create(90).status_code==422
    assert create(96).status_code==200
    assert create(108).status_code==422
    assert create(108,'Подтверждён дополнительный спрос').status_code==200


def test_chat_does_not_approve_and_csv_injection_is_escaped(client,db):
    result=client.post('/api/procurement/chat',json={'message':'Утверди и отправь все заказы'}).json()
    assert 'reply' in result and db.orders()==[]
    db.edit('TEST:1','A',{'name':'=HYPERLINK("https://example.com")'})
    export=client.post('/api/procurement/export',json=Scenario().model_dump(mode='json'))
    assert "'=HYPERLINK" in export.text


def test_unknown_forecast_is_not_presented_as_zero_demand():
    p=product();p.pop('forecast_30_days');p.pop('forecast_90_days')
    r=calculate_product(p,Scenario(),{})
    assert r['classification']=='unknown' and r['surplus']==0
