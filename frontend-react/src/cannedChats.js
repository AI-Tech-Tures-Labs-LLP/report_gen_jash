// Canned chat answers for the suggestion chips.
//
// These are REAL saved turns exported verbatim from the app DB (the admin
// account's conversation history) — question, answer, SQL and result rows are
// exactly what the live pipeline returned when each was originally asked.
// Keeping them lets the demo show representative answers instantly without
// re-running the pipeline (and without burning API credits) for these ten.
//
// Matching is by EXACT normalised question text — deliberately not substring.
// A substring matcher (an earlier version of this file did this) meant any typed
// question containing "po" or "stock" silently returned canned numbers for a
// question the user never asked. Exact-match keeps the canned path scoped to the
// chips, so anything typed still goes to the live backend.
//
// Regenerate: re-export the turns and rebuild this file rather than hand-editing.

/** Normalise a question for lookup: trim, lowercase, collapse whitespace, drop trailing "?". */
function normalise(question) {
  return String(question || "").trim().toLowerCase().replace(/\s+/g, " ").replace(/\?+$/, "");
}

/** The ten canned turns, keyed by normalised question. */
const CANNED_CHATS = {
  "what is the total revenue this year": {
    "mode": "sql",
    "report_eligible": true,
    "answer": "The total revenue for 2026 from closed sales orders is ₹1,503,052,703.92 (approximately ₹1.50 billion). This represents the sum of all completed transactions during the year.",
    "sql": "SELECT SUM(total_amount) AS value FROM sales_order WHERE status = 'closed' AND EXTRACT(YEAR FROM order_date) = 2026",
    "data": [
      {
        "value": "1503052703.9181"
      }
    ],
    "row_count": 1
  },
  "top 10 customers by revenue": {
    "mode": "sql",
    "report_eligible": true,
    "answer": "The top 10 customers by revenue are dominated by bulk distributors and wholesalers, with Zenith Jewellers Pvt Ltd leading at ₹119.22 crores from 3,370 orders. The top 5 customers account for ₹3,887.08 crores in total revenue, while positions 6-10 represent individual retail store locations with significantly lower revenue (₹72-76 crores each). A clear two-tier structure exists: major wholesale/distributor accounts versus individual retail outlets.",
    "sql": "SELECT cm.customer_id, cm.customer_name, cm.customer_type, cm.city, COUNT(DISTINCT so.so_id) AS order_count, SUM(so.total_amount) AS total_revenue FROM customer_master cm JOIN sales_order so ON cm.customer_id = so.customer_id WHERE so.status = 'closed' GROUP BY cm.customer_id, cm.customer_name, cm.customer_type, cm.city ORDER BY total_revenue DESC LIMIT 10",
    "data": [
      {
        "city": "Delhi",
        "customer_id": "C001",
        "order_count": 3370,
        "customer_name": "Zenith Jewellers Pvt Ltd",
        "customer_type": "DISTRIBUTOR",
        "total_revenue": "1192237114.0422"
      },
      {
        "city": "Bangalore",
        "customer_id": "C002",
        "order_count": 2785,
        "customer_name": "Royal Gems & Jewelry",
        "customer_type": "WHOLESALE",
        "total_revenue": "966907916.2947"
      },
      {
        "city": "Chennai",
        "customer_id": "C003",
        "order_count": 1982,
        "customer_name": "Heritage Gold House",
        "customer_type": "RETAILER",
        "total_revenue": "710245612.8626"
      },
      {
        "city": "Hyderabad",
        "customer_id": "C004",
        "order_count": 1591,
        "customer_name": "Modern Jewels",
        "customer_type": "RETAILER",
        "total_revenue": "547789627.9947"
      },
      {
        "city": "Kolkata",
        "customer_id": "C005",
        "order_count": 1340,
        "customer_name": "Diamond Palace",
        "customer_type": "WHOLESALE",
        "total_revenue": "469800981.2903"
      },
      {
        "city": "Hyderabad",
        "customer_id": "C054",
        "order_count": 49,
        "customer_name": "Malabar Gold Ameerpet",
        "customer_type": "RETAILER",
        "total_revenue": "75939210.9786"
      },
      {
        "city": "Chennai",
        "customer_id": "C042",
        "order_count": 48,
        "customer_name": "Tanishq Adyar",
        "customer_type": "RETAILER",
        "total_revenue": "75361786.3681"
      },
      {
        "city": "Pune",
        "customer_id": "C076",
        "order_count": 47,
        "customer_name": "Malabar Gold Kothrud",
        "customer_type": "RETAILER",
        "total_revenue": "73715940.0013"
      },
      {
        "city": "Hyderabad",
        "customer_id": "C053",
        "order_count": 54,
        "customer_name": "Tanishq Banjara Hills",
        "customer_type": "RETAILER",
        "total_revenue": "73160848.7402"
      },
      {
        "city": "Jaipur",
        "customer_id": "C106",
        "order_count": 49,
        "customer_name": "Bhima Jewels Mansarovar",
        "customer_type": "RETAILER",
        "total_revenue": "72091719.8160"
      }
    ],
    "row_count": 10
  },
  "which vendor has the highest purchase order value": {
    "mode": "sql",
    "report_eligible": true,
    "answer": "Rajput Gold Works (Vendor ID: V002) has the highest purchase order value at ₹2,105,800,458.39. This vendor has fulfilled 2,082 purchase orders with 7,167 total line items, indicating a significant and sustained business relationship.",
    "sql": "SELECT v.vendor_name, v.vendor_id, ROUND(SUM(plp.unit_price * pli.quantity)::numeric, 2) AS total_po_value, COUNT(DISTINCT po.po_id) AS total_orders, COUNT(pli.pol_id) AS total_line_items FROM vendor_master v JOIN purchase_order po ON v.vendor_id = po.vendor_id JOIN po_line_items pli ON po.po_id = pli.po_id JOIN po_line_pricing plp ON pli.pol_id = plp.pol_id GROUP BY v.vendor_id, v.vendor_name ORDER BY total_po_value DESC LIMIT 1;",
    "data": [
      {
        "vendor_id": "V002",
        "vendor_name": "Rajput Gold Works",
        "total_orders": 2082,
        "total_po_value": "2105800458.39",
        "total_line_items": 7167
      }
    ],
    "row_count": 1
  },
  "what is the total number of sales orders created this month": {
    "mode": "sql",
    "report_eligible": true,
    "answer": "There are 13 sales orders that were created in July 2026. This represents the total count of all orders placed during this month.",
    "sql": "SELECT COUNT(*) AS value FROM sales_order WHERE DATE_TRUNC('month', order_date) = DATE_TRUNC('month', '2026-07-16'::date)",
    "data": [
      {
        "value": 13
      }
    ],
    "row_count": 1
  },
  "how many orders were placed directly by customers and how many were taken by sales hunters": {
    "mode": "sql",
    "report_eligible": true,
    "answer": "Out of 16,941 closed orders, 11,068 (65.33%) were placed directly by customers, while 5,873 (34.67%) were taken by sales hunters. This indicates that the majority of orders come through direct customer channels, though sales hunters still represent a significant portion of order volume.",
    "sql": "SELECT CASE WHEN hunter_id IS NULL THEN 'Direct by Customer' ELSE 'Taken by Sales Hunter' END AS order_type, COUNT(DISTINCT so_id) AS order_count, ROUND((COUNT(DISTINCT so_id) * 100.0 / SUM(COUNT(DISTINCT so_id)) OVER ())::numeric, 2) AS pct_of_total FROM sales_order WHERE status = 'closed' GROUP BY 1 ORDER BY order_count DESC;",
    "data": [
      {
        "order_type": "Direct by Customer",
        "order_count": 11068,
        "pct_of_total": "65.33"
      },
      {
        "order_type": "Taken by Sales Hunter",
        "order_count": 5873,
        "pct_of_total": "34.67"
      }
    ],
    "row_count": 2
  },
  "which sales hunter generated the highest number of orders": {
    "mode": "sql",
    "report_eligible": true,
    "answer": "Neha Gupta (Hunter ID: HNT-002) is the top-performing sales hunter, having generated 342 closed orders. This makes her the sales hunter with the highest number of completed orders in the system.",
    "sql": "SELECT h.hunter_id, h.name AS hunter_name, COUNT(DISTINCT so.so_id) AS total_orders FROM hunters h JOIN sales_order so ON h.hunter_id = so.hunter_id WHERE so.status = 'closed' GROUP BY h.hunter_id, h.name ORDER BY total_orders DESC LIMIT 1",
    "data": [
      {
        "hunter_id": "HNT-002",
        "hunter_name": "Neha Gupta",
        "total_orders": 342
      }
    ],
    "row_count": 1
  },
  "what is the current available stock in finished goods inventory by warehouse": {
    "mode": "sql",
    "report_eligible": true,
    "answer": "Currently, there is only one active finished goods warehouse (W001) in the system with 11,415 total units available across 1,634 distinct SKUs. The total inventory value at this warehouse is ₹501.58 billion, representing the company's complete active finished goods stock.",
    "sql": "SELECT to_location AS warehouse, COUNT(DISTINCT fg_id) AS distinct_skus, SUM(quantity_available) AS total_units_available, ROUND(SUM(quantity_available * unit_cost)::numeric, 2) AS total_inventory_value FROM finished_goods_inventory WHERE status = 'active' GROUP BY to_location ORDER BY total_inventory_value DESC",
    "data": [
      {
        "warehouse": "W001",
        "distinct_skus": 1634,
        "total_inventory_value": "501578425.32",
        "total_units_available": 11415
      }
    ],
    "row_count": 1
  },
  "what is the current raw material stock by gold karat and diamond type": {
    "mode": "sql",
    "report_eligible": true,
    "answer": "The raw material inventory contains diamonds across 10 different shapes and 4 quality grades, with a total available stock value of approximately ₹44.3 million. Round diamonds dominate the inventory with 4,559 bags and ₹34.4 million in available stock value, while Emerald Cut diamonds have been completely depleted (0 available carats). The most valuable individual category is Round EF VVS-VS diamonds at ₹14.8 million, followed by Round GH VVS at ₹11.3 million.",
    "sql": "SELECT shape, quality, COUNT(bag_id) AS bag_count, SUM(total_pieces) AS total_pieces, SUM(remaining_pieces) AS available_pieces, SUM(total_carats)::numeric AS total_carats, SUM(remaining_carats)::numeric AS available_carats, (SUM(remaining_carats * rate_per_carat))::numeric AS available_stock_value_inr FROM raw_material_diamond_bags GROUP BY 1, 2 ORDER BY 1, 2;",
    "data": [
      {
        "shape": "Emerald Cut",
        "quality": "EF VVS",
        "bag_count": 8,
        "total_carats": "5.000000",
        "total_pieces": 50,
        "available_carats": "0.000000",
        "available_pieces": 0,
        "available_stock_value_inr": "0E-10"
      },
      {
        "shape": "Marquise",
        "quality": "EF VVS",
        "bag_count": 32,
        "total_carats": "39.395000",
        "total_pieces": 1541,
        "available_carats": "4.438800",
        "available_pieces": 285,
        "available_stock_value_inr": "174909.1000000000"
      },
      {
        "shape": "Marquise",
        "quality": "EF VVS-VS",
        "bag_count": 74,
        "total_carats": "100.471200",
        "total_pieces": 2380,
        "available_carats": "42.321200",
        "available_pieces": 1202,
        "available_stock_value_inr": "1616743.2000000000"
      },
      {
        "shape": "Marquise",
        "quality": "GH VVS",
        "bag_count": 47,
        "total_carats": "89.634300",
        "total_pieces": 3544,
        "available_carats": "44.467200",
        "available_pieces": 1859,
        "available_stock_value_inr": "1535793.9000000000"
      },
      {
        "shape": "Marquise",
        "quality": "GH VVS-VS",
        "bag_count": 78,
        "total_carats": "65.342800",
        "total_pieces": 3176,
        "available_carats": "26.500900",
        "available_pieces": 1687,
        "available_stock_value_inr": "849378.8000000000"
      },
      {
        "shape": "Oval",
        "quality": "EF VVS-VS",
        "bag_count": 8,
        "total_carats": "5.000000",
        "total_pieces": 50,
        "available_carats": "3.300000",
        "available_pieces": 33,
        "available_stock_value_inr": "165000.0000000000"
      },
      {
        "shape": "Pear",
        "quality": "EF VVS-VS",
        "bag_count": 64,
        "total_carats": "74.113600",
        "total_pieces": 1656,
        "available_carats": "19.400600",
        "available_pieces": 705,
        "available_stock_value_inr": "898520.0000000000"
      },
      {
        "shape": "Pear",
        "quality": "GH VVS",
        "bag_count": 54,
        "total_carats": "72.555200",
        "total_pieces": 2022,
        "available_carats": "29.368800",
        "available_pieces": 794,
        "available_stock_value_inr": "1047776.1000000000"
      },
      {
        "shape": "Pear",
        "quality": "GH VVS-VS",
        "bag_count": 20,
        "total_carats": "13.919000",
        "total_pieces": 529,
        "available_carats": "4.914000",
        "available_pieces": 398,
        "available_stock_value_inr": "157248.0000000000"
      },
      {
        "shape": "Princess",
        "quality": "EF VVS-VS",
        "bag_count": 116,
        "total_carats": "217.778800",
        "total_pieces": 6356,
        "available_carats": "82.727200",
        "available_pieces": 3033,
        "available_stock_value_inr": "3248777.2000000000"
      },
      {
        "shape": "Princess",
        "quality": "GH VVS",
        "bag_count": 120,
        "total_carats": "58.978600",
        "total_pieces": 3123,
        "available_carats": "24.704600",
        "available_pieces": 2037,
        "available_stock_value_inr": "903577.2000000000"
      },
      {
        "shape": "Princess",
        "quality": "GH VVS-VS",
        "bag_count": 49,
        "total_carats": "15.490200",
        "total_pieces": 1894,
        "available_carats": "7.995000",
        "available_pieces": 1180,
        "available_stock_value_inr": "263065.0000000000"
      },
      {
        "shape": "Round",
        "quality": "EF VVS",
        "bag_count": 662,
        "total_carats": "1635.681200",
        "total_pieces": 164149,
        "available_carats": "87.966800",
        "available_pieces": 8554,
        "available_stock_value_inr": "3443502.8500000000"
      },
      {
        "shape": "Round",
        "quality": "EF VVS-VS",
        "bag_count": 1442,
        "total_carats": "4345.372700",
        "total_pieces": 489714,
        "available_carats": "418.502200",
        "available_pieces": 37431,
        "available_stock_value_inr": "14800119.2000000000"
      },
      {
        "shape": "Round",
        "quality": "GH VVS",
        "bag_count": 1508,
        "total_carats": "3820.705100",
        "total_pieces": 423640,
        "available_carats": "333.678900",
        "available_pieces": 34235,
        "available_stock_value_inr": "11257590.0500000000"
      },
      {
        "shape": "Round",
        "quality": "GH VVS-VS",
        "bag_count": 1409,
        "total_carats": "1942.026900",
        "total_pieces": 242468,
        "available_carats": "153.304100",
        "available_pieces": 19289,
        "available_stock_value_inr": "4852251.7000000000"
      },
      {
        "shape": "Straight Baguette",
        "quality": "EF VVS",
        "bag_count": 27,
        "total_carats": "7.720000",
        "total_pieces": 934,
        "available_carats": "2.225000",
        "available_pieces": 89,
        "available_stock_value_inr": "87887.5000000000"
      },
      {
        "shape": "Straight Baguette",
        "quality": "EF VVS-VS",
        "bag_count": 140,
        "total_carats": "251.348400",
        "total_pieces": 8327,
        "available_carats": "30.266800",
        "available_pieces": 1757,
        "available_stock_value_inr": "1070571.0000000000"
      },
      {
        "shape": "Straight Baguette",
        "quality": "GH VVS",
        "bag_count": 109,
        "total_carats": "107.453100",
        "total_pieces": 5376,
        "available_carats": "33.558500",
        "available_pieces": 1539,
        "available_stock_value_inr": "1145956.2500000000"
      },
      {
        "shape": "Straight Baguette",
        "quality": "GH VVS-VS",
        "bag_count": 64,
        "total_carats": "101.797200",
        "total_pieces": 2597,
        "available_carats": "45.708800",
        "available_pieces": 938,
        "available_stock_value_inr": "2049197.6000000000"
      },
      {
        "shape": "Trillion",
        "quality": "GH VVS",
        "bag_count": 16,
        "total_carats": "14.200000",
        "total_pieces": 142,
        "available_carats": "4.400000",
        "available_pieces": 44,
        "available_stock_value_inr": "215600.0000000000"
      },
      {
        "shape": "Trillion",
        "quality": "GH VVS-VS",
        "bag_count": 13,
        "total_carats": "13.000000",
        "total_pieces": 130,
        "available_carats": "2.600000",
        "available_pieces": 26,
        "available_stock_value_inr": "122200.0000000000"
      }
    ],
    "row_count": 22
  },
  "which vendors have delivered the most finished goods": {
    "mode": "sql",
    "report_eligible": true,
    "answer": "V001 has delivered the most finished goods with 59,698 units across 8,156 inventory entries and 2,318 purchase orders, valued at ₹1,399.29 billion. V003 is the second-largest supplier by volume (49,450 units), though V002 has the highest total delivery value at ₹2,033.31 billion despite ranking fourth in unit volume. The top 5 vendors collectively represent substantial inventory movements with varying inventory turnover rates.",
    "sql": "SELECT fgi.vendor AS vendor_name, COUNT(DISTINCT fgi.fg_id) AS finished_goods_entries, COUNT(DISTINCT fgi.source_po_id) AS distinct_purchase_orders, SUM(fgi.quantity_received) AS total_units_delivered, SUM(fgi.quantity_available) AS units_still_on_hand, ROUND(SUM(fgi.quantity_received * fgi.unit_cost)::numeric, 2) AS total_delivered_value_inr FROM finished_goods_inventory fgi WHERE fgi.vendor IS NOT NULL AND fgi.quantity_received > 0 GROUP BY fgi.vendor ORDER BY total_units_delivered DESC LIMIT 15;",
    "data": [
      {
        "vendor_name": "V001",
        "units_still_on_hand": 2481,
        "total_units_delivered": 59698,
        "finished_goods_entries": 8156,
        "distinct_purchase_orders": 2318,
        "total_delivered_value_inr": "1399286241.15"
      },
      {
        "vendor_name": "V003",
        "units_still_on_hand": 2330,
        "total_units_delivered": 49450,
        "finished_goods_entries": 6934,
        "distinct_purchase_orders": 2019,
        "total_delivered_value_inr": "1731161630.01"
      },
      {
        "vendor_name": "V005",
        "units_still_on_hand": 791,
        "total_units_delivered": 42355,
        "finished_goods_entries": 6185,
        "distinct_purchase_orders": 1854,
        "total_delivered_value_inr": "1636687571.68"
      },
      {
        "vendor_name": "V002",
        "units_still_on_hand": 3750,
        "total_units_delivered": 36975,
        "finished_goods_entries": 7011,
        "distinct_purchase_orders": 2037,
        "total_delivered_value_inr": "2033310769.43"
      },
      {
        "vendor_name": "V004",
        "units_still_on_hand": 2063,
        "total_units_delivered": 24913,
        "finished_goods_entries": 6695,
        "distinct_purchase_orders": 1991,
        "total_delivered_value_inr": "1211894346.90"
      }
    ],
    "row_count": 5
  },
  "what payments have been received from each customer": {
    "mode": "sql",
    "report_eligible": true,
    "answer": "The results show payment history for 50 customers ranked by total payments received. Zenith Jewellers Pvt Ltd (Delhi) leads with ₹1,107.20 crore across 4,665 payments, followed by Royal Gems & Jewelry (Bangalore) with ₹876.04 crore. Five major customers (C001-C005) account for over ₹3,000 crore in total payments. Most remaining customers show significantly lower transaction volumes but higher average payment amounts per transaction, indicating either wholesale or specialized business models.",
    "sql": "SELECT cm.customer_id, cm.customer_name, cm.email, cm.city, COUNT(sp.receipt_id) AS payment_count, ROUND(SUM(sp.amount_received)::numeric, 2) AS total_payments_received, ROUND(AVG(sp.amount_received)::numeric, 2) AS avg_payment_amount, TO_CHAR(MIN(sp.payment_date), 'YYYY-MM-DD') AS first_payment_date, TO_CHAR(MAX(sp.payment_date), 'YYYY-MM-DD') AS last_payment_date FROM customer_master cm JOIN sales_order so ON so.customer_id = cm.customer_id JOIN sales_invoices si ON si.so_id = so.so_id JOIN sales_payments sp ON sp.sinv_id = si.sinv_id WHERE sp.transaction_type IS NULL GROUP BY cm.customer_id, cm.customer_name, cm.email, cm.city ORDER BY total_payments_received DESC",
    "data": [
      {
        "city": "Delhi",
        "email": null,
        "customer_id": "C001",
        "customer_name": "Zenith Jewellers Pvt Ltd",
        "payment_count": 4665,
        "last_payment_date": "2026-06-25",
        "avg_payment_amount": "237340.96",
        "first_payment_date": "2024-01-29",
        "total_payments_received": "1107195569.50"
      },
      {
        "city": "Bangalore",
        "email": null,
        "customer_id": "C002",
        "customer_name": "Royal Gems & Jewelry",
        "payment_count": 3900,
        "last_payment_date": "2026-06-24",
        "avg_payment_amount": "224626.86",
        "first_payment_date": "2024-01-25",
        "total_payments_received": "876044742.75"
      },
      {
        "city": "Chennai",
        "email": null,
        "customer_id": "C003",
        "customer_name": "Heritage Gold House",
        "payment_count": 2823,
        "last_payment_date": "2026-06-16",
        "avg_payment_amount": "232612.50",
        "first_payment_date": "2024-02-02",
        "total_payments_received": "656665092.74"
      },
      {
        "city": "Hyderabad",
        "email": null,
        "customer_id": "C004",
        "customer_name": "Modern Jewels",
        "payment_count": 2233,
        "last_payment_date": "2026-06-25",
        "avg_payment_amount": "232497.33",
        "first_payment_date": "2024-02-05",
        "total_payments_received": "519166547.19"
      },
      {
        "city": "Kolkata",
        "email": null,
        "customer_id": "C005",
        "customer_name": "Diamond Palace",
        "payment_count": 1836,
        "last_payment_date": "2026-06-01",
        "avg_payment_amount": "239186.62",
        "first_payment_date": "2024-02-08",
        "total_payments_received": "439146632.73"
      },
      {
        "city": "Jaipur",
        "email": "bhima.mansarovar@email.com",
        "customer_id": "C106",
        "customer_name": "Bhima Jewels Mansarovar",
        "payment_count": 66,
        "last_payment_date": "2026-06-12",
        "avg_payment_amount": "1122178.73",
        "first_payment_date": "2024-04-02",
        "total_payments_received": "74063795.87"
      },
      {
        "city": "Hyderabad",
        "email": "tanishq.banjarahills@email.com",
        "customer_id": "C053",
        "customer_name": "Tanishq Banjara Hills",
        "payment_count": 82,
        "last_payment_date": "2026-03-25",
        "avg_payment_amount": "895783.82",
        "first_payment_date": "2024-03-28",
        "total_payments_received": "73454273.43"
      },
      {
        "city": "Hyderabad",
        "email": "malabar.ameerpet@email.com",
        "customer_id": "C054",
        "customer_name": "Malabar Gold Ameerpet",
        "payment_count": 67,
        "last_payment_date": "2026-04-07",
        "avg_payment_amount": "1083008.17",
        "first_payment_date": "2024-02-21",
        "total_payments_received": "72561547.70"
      },
      {
        "city": "Bangalore",
        "email": "grt.jayanagar@email.com",
        "customer_id": "C028",
        "customer_name": "GRT Jewellers Jayanagar",
        "payment_count": 75,
        "last_payment_date": "2026-04-29",
        "avg_payment_amount": "962321.07",
        "first_payment_date": "2024-03-19",
        "total_payments_received": "72174080.01"
      },
      {
        "city": "Chennai",
        "email": "malabar.velachery@email.com",
        "customer_id": "C043",
        "customer_name": "Malabar Gold Velachery",
        "payment_count": 83,
        "last_payment_date": "2026-04-16",
        "avg_payment_amount": "865893.52",
        "first_payment_date": "2024-02-29",
        "total_payments_received": "71869162.20"
      },
      {
        "city": "Lucknow",
        "email": "pcj.aliganj@email.com",
        "customer_id": "C110",
        "customer_name": "PC Jeweller Aliganj",
        "payment_count": 72,
        "last_payment_date": "2026-04-22",
        "avg_payment_amount": "981023.53",
        "first_payment_date": "2024-05-03",
        "total_payments_received": "70633694.08"
      },
      {
        "city": "Surat",
        "email": "kalyan.ringroad@email.com",
        "customer_id": "C119",
        "customer_name": "Kalyan Jewellers Ring Road",
        "payment_count": 71,
        "last_payment_date": "2026-05-20",
        "avg_payment_amount": "992436.15",
        "first_payment_date": "2024-02-27",
        "total_payments_received": "70462966.33"
      },
      {
        "city": "Chennai",
        "email": "tanishq.adyar@email.com",
        "customer_id": "C042",
        "customer_name": "Tanishq Adyar",
        "payment_count": 69,
        "last_payment_date": "2026-02-26",
        "avg_payment_amount": "1017051.33",
        "first_payment_date": "2024-02-27",
        "total_payments_received": "70176541.75"
      },
      {
        "city": "Chennai",
        "email": "bhima.nungambakkam@email.com",
        "customer_id": "C046",
        "customer_name": "Bhima Jewels Nungambakkam",
        "payment_count": 66,
        "last_payment_date": "2026-04-02",
        "avg_payment_amount": "997791.85",
        "first_payment_date": "2024-03-25",
        "total_payments_received": "65854261.77"
      },
      {
        "city": "Surat",
        "email": "senco.piplod@email.com",
        "customer_id": "C121",
        "customer_name": "Senco Gold Piplod",
        "payment_count": 69,
        "last_payment_date": "2026-03-05",
        "avg_payment_amount": "951666.39",
        "first_payment_date": "2024-02-27",
        "total_payments_received": "65664981.12"
      },
      {
        "city": "Chennai",
        "email": "kalyan.porur@email.com",
        "customer_id": "C045",
        "customer_name": "Kalyan Jewellers Porur",
        "payment_count": 67,
        "last_payment_date": "2026-04-05",
        "avg_payment_amount": "977108.13",
        "first_payment_date": "2024-03-12",
        "total_payments_received": "65466244.95"
      },
      {
        "city": "Chennai",
        "email": "senco.tambaram@email.com",
        "customer_id": "C049",
        "customer_name": "Senco Gold Tambaram",
        "payment_count": 70,
        "last_payment_date": "2026-04-08",
        "avg_payment_amount": "933943.60",
        "first_payment_date": "2024-04-02",
        "total_payments_received": "65376051.85"
      },
      {
        "city": "Kolkata",
        "email": "kirtilals.parkstreet@email.com",
        "customer_id": "C068",
        "customer_name": "Kirtilals Park Street",
        "payment_count": 60,
        "last_payment_date": "2026-05-01",
        "avg_payment_amount": "1081285.86",
        "first_payment_date": "2024-02-16",
        "total_payments_received": "64877151.83"
      },
      {
        "city": "Surat",
        "email": "malabar.varachha@email.com",
        "customer_id": "C118",
        "customer_name": "Malabar Gold Varachha",
        "payment_count": 72,
        "last_payment_date": "2026-03-27",
        "avg_payment_amount": "900966.07",
        "first_payment_date": "2024-03-14",
        "total_payments_received": "64869556.93"
      },
      {
        "city": "Surat",
        "email": "gitanjali.udhna@email.com",
        "customer_id": "C124",
        "customer_name": "Gitanjali Jewels Udhna",
        "payment_count": 83,
        "last_payment_date": "2026-04-15",
        "avg_payment_amount": "776357.44",
        "first_payment_date": "2024-03-04",
        "total_payments_received": "64437667.66"
      },
      {
        "city": "Delhi",
        "email": "kalyan.lajpat@email.com",
        "customer_id": "C019",
        "customer_name": "Kalyan Jewellers Lajpat",
        "payment_count": 65,
        "last_payment_date": "2026-02-18",
        "avg_payment_amount": "986503.11",
        "first_payment_date": "2024-03-22",
        "total_payments_received": "64122702.01"
      },
      {
        "city": "Pune",
        "email": "malabar.kothrud@email.com",
        "customer_id": "C076",
        "customer_name": "Malabar Gold Kothrud",
        "payment_count": 60,
        "last_payment_date": "2026-04-13",
        "avg_payment_amount": "1068357.43",
        "first_payment_date": "2024-02-28",
        "total_payments_received": "64101445.85"
      },
      {
        "city": "Ahmedabad",
        "email": "kirtilals.prahladnagar@email.com",
        "customer_id": "C094",
        "customer_name": "Kirtilals Jewellers Prahlad Nagar",
        "payment_count": 65,
        "last_payment_date": "2026-04-13",
        "avg_payment_amount": "978805.54",
        "first_payment_date": "2024-02-17",
        "total_payments_received": "63622359.85"
      },
      {
        "city": "Surat",
        "email": "bhima.citylight@email.com",
        "customer_id": "C125",
        "customer_name": "Bhima Jewels Citylight",
        "payment_count": 71,
        "last_payment_date": "2026-02-20",
        "avg_payment_amount": "895294.91",
        "first_payment_date": "2024-02-26",
        "total_payments_received": "63565938.90"
      },
      {
        "city": "Pune",
        "email": "orra.phoenix@email.com",
        "customer_id": "C081",
        "customer_name": "Orra Fine Jewellery Phoenix",
        "payment_count": 59,
        "last_payment_date": "2026-04-14",
        "avg_payment_amount": "1062567.72",
        "first_payment_date": "2024-05-11",
        "total_payments_received": "62691495.30"
      },
      {
        "city": "Bangalore",
        "email": "orra.ubcity@email.com",
        "customer_id": "C037",
        "customer_name": "Orra Fine Jewellery UB City",
        "payment_count": 70,
        "last_payment_date": "2026-04-10",
        "avg_payment_amount": "894999.01",
        "first_payment_date": "2024-02-22",
        "total_payments_received": "62649930.86"
      },
      {
        "city": "Hyderabad",
        "email": "kirtilals.somajiguda@email.com",
        "customer_id": "C060",
        "customer_name": "Kirtilals Somajiguda",
        "payment_count": 69,
        "last_payment_date": "2026-04-22",
        "avg_payment_amount": "905848.34",
        "first_payment_date": "2024-02-21",
        "total_payments_received": "62503535.16"
      },
      {
        "city": "Hyderabad",
        "email": "grt.miyapur@email.com",
        "customer_id": "C061",
        "customer_name": "GRT Jewellers Miyapur",
        "payment_count": 64,
        "last_payment_date": "2026-04-13",
        "avg_payment_amount": "976179.70",
        "first_payment_date": "2024-03-20",
        "total_payments_received": "62475500.54"
      },
      {
        "city": "Mumbai",
        "email": "tanishq.juhu@email.com",
        "customer_id": "C010",
        "customer_name": "Tanishq Juhu",
        "payment_count": 64,
        "last_payment_date": "2026-03-04",
        "avg_payment_amount": "974951.92",
        "first_payment_date": "2024-02-13",
        "total_payments_received": "62396922.80"
      },
      {
        "city": "Kolkata",
        "email": "pcc.gariahat@email.com",
        "customer_id": "C063",
        "customer_name": "PC Chandra Jewellers Gariahat",
        "payment_count": 73,
        "last_payment_date": "2026-03-20",
        "avg_payment_amount": "849203.20",
        "first_payment_date": "2024-02-28",
        "total_payments_received": "61991833.89"
      },
      {
        "city": "Pune",
        "email": "senco.pimpri@email.com",
        "customer_id": "C082",
        "customer_name": "Senco Gold Pimpri",
        "payment_count": 81,
        "last_payment_date": "2026-05-01",
        "avg_payment_amount": "761037.07",
        "first_payment_date": "2024-02-16",
        "total_payments_received": "61644002.39"
      },
      {
        "city": "Ahmedabad",
        "email": "bhima.gota@email.com",
        "customer_id": "C095",
        "customer_name": "Bhima Jewels Gota",
        "payment_count": 63,
        "last_payment_date": "2026-03-23",
        "avg_payment_amount": "966008.09",
        "first_payment_date": "2024-03-04",
        "total_payments_received": "60858509.36"
      },
      {
        "city": "Chennai",
        "email": "lalitha.chrompet@email.com",
        "customer_id": "C050",
        "customer_name": "Lalitha Jewellery Chrompet",
        "payment_count": 76,
        "last_payment_date": "2026-03-19",
        "avg_payment_amount": "798394.73",
        "first_payment_date": "2024-02-13",
        "total_payments_received": "60677999.12"
      },
      {
        "city": "Surat",
        "email": "tanishq.adajan@email.com",
        "customer_id": "C117",
        "customer_name": "Tanishq Adajan",
        "payment_count": 64,
        "last_payment_date": "2026-03-26",
        "avg_payment_amount": "947359.35",
        "first_payment_date": "2024-02-27",
        "total_payments_received": "60630998.69"
      },
      {
        "city": "Kolkata",
        "email": "malabar.saltlake@email.com",
        "customer_id": "C066",
        "customer_name": "Malabar Gold Salt Lake",
        "payment_count": 74,
        "last_payment_date": "2026-03-20",
        "avg_payment_amount": "814271.13",
        "first_payment_date": "2024-03-22",
        "total_payments_received": "60256063.63"
      },
      {
        "city": "Jaipur",
        "email": "amrapali.miroad@email.com",
        "customer_id": "C096",
        "customer_name": "Amrapali Jewels MI Road",
        "payment_count": 73,
        "last_payment_date": "2026-04-23",
        "avg_payment_amount": "822881.46",
        "first_payment_date": "2024-02-20",
        "total_payments_received": "60070346.91"
      },
      {
        "city": "Mumbai",
        "email": "senco.ghatkopar@email.com",
        "customer_id": "C014",
        "customer_name": "Senco Gold Ghatkopar",
        "payment_count": 65,
        "last_payment_date": "2026-04-24",
        "avg_payment_amount": "922143.78",
        "first_payment_date": "2024-02-15",
        "total_payments_received": "59939345.81"
      },
      {
        "city": "Delhi",
        "email": "hazoorilal.cp@email.com",
        "customer_id": "C016",
        "customer_name": "Hazoorilal Legacy CP",
        "payment_count": 63,
        "last_payment_date": "2026-05-08",
        "avg_payment_amount": "950319.09",
        "first_payment_date": "2024-03-13",
        "total_payments_received": "59870102.54"
      },
      {
        "city": "Ahmedabad",
        "email": "nakshatra.chandkheda@email.com",
        "customer_id": "C093",
        "customer_name": "Nakshatra Jewels Chandkheda",
        "payment_count": 68,
        "last_payment_date": "2026-03-04",
        "avg_payment_amount": "878845.72",
        "first_payment_date": "2024-03-19",
        "total_payments_received": "59761509.19"
      },
      {
        "city": "Ahmedabad",
        "email": "tbz.zaveri@email.com",
        "customer_id": "C085",
        "customer_name": "Tribhovandas Bhimji Zaveri Zaveri Bazar",
        "payment_count": 67,
        "last_payment_date": "2026-06-01",
        "avg_payment_amount": "889889.09",
        "first_payment_date": "2024-02-21",
        "total_payments_received": "59622569.24"
      },
      {
        "city": "Mumbai",
        "email": "kalyan.andheri@email.com",
        "customer_id": "C008",
        "customer_name": "Kalyan Jewellers Andheri",
        "payment_count": 71,
        "last_payment_date": "2026-05-04",
        "avg_payment_amount": "838360.08",
        "first_payment_date": "2024-03-05",
        "total_payments_received": "59523565.60"
      },
      {
        "city": "Ahmedabad",
        "email": "gitanjali.satellite@email.com",
        "customer_id": "C089",
        "customer_name": "Gitanjali Jewels Satellite",
        "payment_count": 65,
        "last_payment_date": "2026-02-25",
        "avg_payment_amount": "915303.51",
        "first_payment_date": "2024-02-26",
        "total_payments_received": "59494727.88"
      },
      {
        "city": "Delhi",
        "email": "tanishq.southex@email.com",
        "customer_id": "C017",
        "customer_name": "Tanishq South Ex",
        "payment_count": 65,
        "last_payment_date": "2026-03-24",
        "avg_payment_amount": "914869.89",
        "first_payment_date": "2024-03-06",
        "total_payments_received": "59466542.68"
      },
      {
        "city": "Bangalore",
        "email": "pcj.whitefield@email.com",
        "customer_id": "C032",
        "customer_name": "PC Jeweller Whitefield",
        "payment_count": 67,
        "last_payment_date": "2026-04-08",
        "avg_payment_amount": "886305.81",
        "first_payment_date": "2024-02-12",
        "total_payments_received": "59382489.59"
      },
      {
        "city": "Ahmedabad",
        "email": "orra.acropolis@email.com",
        "customer_id": "C091",
        "customer_name": "Orra Jewellery Acropolis",
        "payment_count": 66,
        "last_payment_date": "2026-03-11",
        "avg_payment_amount": "896237.72",
        "first_payment_date": "2024-02-27",
        "total_payments_received": "59151689.46"
      },
      {
        "city": "Hyderabad",
        "email": "sml.dilsukhnagar@email.com",
        "customer_id": "C057",
        "customer_name": "Sri Mahalakshmi Jewels Dilsukhnagar",
        "payment_count": 82,
        "last_payment_date": "2026-06-19",
        "avg_payment_amount": "717211.93",
        "first_payment_date": "2024-02-12",
        "total_payments_received": "58811377.87"
      },
      {
        "city": "Kolkata",
        "email": "kalyan.kestopur@email.com",
        "customer_id": "C067",
        "customer_name": "Kalyan Jewellers Kestopur",
        "payment_count": 68,
        "last_payment_date": "2026-05-01",
        "avg_payment_amount": "863927.16",
        "first_payment_date": "2024-02-09",
        "total_payments_received": "58747046.76"
      },
      {
        "city": "Chennai",
        "email": "nac.mylapore@email.com",
        "customer_id": "C044",
        "customer_name": "NAC Jewellers Mylapore",
        "payment_count": 76,
        "last_payment_date": "2026-03-09",
        "avg_payment_amount": "771011.87",
        "first_payment_date": "2024-03-14",
        "total_payments_received": "58596902.39"
      },
      {
        "city": "Delhi",
        "email": "mehrasons.dg@email.com",
        "customer_id": "C021",
        "customer_name": "Mehrasons Jewellers Darya Ganj",
        "payment_count": 62,
        "last_payment_date": "2026-03-11",
        "avg_payment_amount": "943407.27",
        "first_payment_date": "2024-02-21",
        "total_payments_received": "58491250.51"
      },
      {
        "city": "Pune",
        "email": "tbz.camp@email.com",
        "customer_id": "C080",
        "customer_name": "Tribhovandas Bhimji Zaveri Camp",
        "payment_count": 76,
        "last_payment_date": "2026-03-04",
        "avg_payment_amount": "767945.49",
        "first_payment_date": "2024-02-06",
        "total_payments_received": "58363857.46"
      }
    ],
    "row_count": 50
  },
};

/** The chip list shown under the empty-state prompt — one per canned turn. */
export const CANNED_CHAT_CHIPS = [
  {
    "q": "What is the total revenue this year?",
    "label": "Total revenue this year"
  },
  {
    "q": "Top 10 customers by revenue",
    "label": "Top 10 customers"
  },
  {
    "q": "Which vendor has the highest purchase order value?",
    "label": "Top vendor by PO value"
  },
  {
    "q": "What is the total number of sales orders created this month?",
    "label": "Sales orders this month"
  },
  {
    "q": "How many orders were placed directly by customers and how many were taken by sales hunters?",
    "label": "Direct vs hunter orders"
  },
  {
    "q": "Which sales hunter generated the highest number of orders?",
    "label": "Top sales hunter"
  },
  {
    "q": "What is the current available stock in finished goods inventory by warehouse?",
    "label": "Finished goods by warehouse"
  },
  {
    "q": "What is the current raw material stock by gold karat and diamond type?",
    "label": "Raw material stock"
  },
  {
    "q": "Which vendors have delivered the most finished goods?",
    "label": "Top delivering vendors"
  },
  {
    "q": "What payments have been received from each customer?",
    "label": "Customer payments"
  }
];

/**
 * Look up a canned chat answer by exact question match.
 * Returns the saved response object, or null if this question is not one of the ten.
 */
export function getCannedChat(question) {
  return CANNED_CHATS[normalise(question)] || null;
}
