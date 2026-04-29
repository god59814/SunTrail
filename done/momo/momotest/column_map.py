# column_map.py
COLUMN_MAP = {
    "ERP品號": "batchSupNo",
    "國際條碼": "internationalNo",
    "庫存": "prepareQty",

    "銷售品名(相同當作同一賣場)": "supGoodsName_salePoint",
    "商品特色標語": "supGoodsName_serial",
    "品牌": "supGoodsName_brand",

    "規格名稱1": "colSeq1",
    "規格內容1": "colDetail1",
    "規格名稱2": "colSeq2",
    "規格內容2": "colDetail2",

    "價格\n售價": "salePrice",
    "價格\n建議售價": "custPrice",
    "價格\n進價/成本價": "buyPrice",

    "(momo)產地": "originCode",
    "商品材積\n長(cm)": "length",
    "商品材積\n寬(cm)": "width",
    "商品材積\n高(cm)": "height",
    "重量(KG)": "weight",
    "商品型號\n(momo大家電必填)": "largeMachineModel",

    # 你如果要把 youtube 放在 mobileDetailInfo，再另外處理
    "Youtube網址": "youtube_url",
}
