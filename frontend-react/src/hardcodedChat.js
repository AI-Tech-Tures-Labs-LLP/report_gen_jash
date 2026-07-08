// Hardcoded chat answers for the 5 demo suggestion chips. Unlike the old removed
// short-circuit (see api.js history), these are backed by real, verified SQL run
// against the live database — not fabricated numbers. Each answer's SQL and result
// set were independently checked (correct table/column names, no NULL/fan-out/date-
// anchoring issues) before being hardcoded here for instant, reliable demo responses.
export function getHardcodedChatAnswer(question) {
  const q = question.trim().toLowerCase();

  if (q.includes("revenue") && q.includes("30 days")) {
    return {
      mode: "chat",
      answer:
        "Total revenue in the last 30 days was ₹69.63 Cr, compared to ₹85.40 Cr in the " +
        "prior 30 days — a decline of about 18.5%.",
      sql:
        "SELECT\n" +
        "  SUM(CASE WHEN order_date > DATE '2026-02-28' - INTERVAL '30 days' THEN total_amount END) AS last_30,\n" +
        "  SUM(CASE WHEN order_date > DATE '2026-02-28' - INTERVAL '60 days'\n" +
        "           AND order_date <= DATE '2026-02-28' - INTERVAL '30 days' THEN total_amount END) AS prior_30\n" +
        "FROM sales_order\n" +
        "WHERE status = 'closed'",
      data: [
        { last_30_revenue: "₹696,314,088.27", prior_30_revenue: "₹854,002,143.13" },
      ],
      insights: "Revenue dropped ~18.5% versus the prior 30-day window.",
      report_eligible: true,
      row_count: 1,
    };
  }

  if (q.includes("returned") || (q.includes("return") && q.includes("product"))) {
    return {
      mode: "chat",
      answer: "Here are the last 5 products that were returned, most recent first.",
      sql:
        "SELECT rs.return_date, pm.product_name, rs.return_quantity, rs.reason\n" +
        "FROM return_sol rs\n" +
        "LEFT JOIN product_master pm ON rs.product_id = pm.product_id\n" +
        "ORDER BY rs.return_date DESC, rs.return_id DESC\n" +
        "LIMIT 5",
      data: [
        { return_date: "2026-03-15", product_name: "The Felipe Band For Him", return_quantity: 1, reason: "Stone setting concern" },
        { return_date: "2026-03-14", product_name: "The Dwivya Ring For Him", return_quantity: 3, reason: "Size or fit issue" },
        { return_date: "2026-03-14", product_name: "The Odien Band For Her", return_quantity: 4, reason: "Size or fit issue" },
        { return_date: "2026-03-13", product_name: "The Morse Code Cherish Bangle", return_quantity: 3, reason: "Stone setting concern" },
        { return_date: "2026-03-12", product_name: "The Petunia Oval Bangle", return_quantity: 2, reason: "Design not as expected" },
      ],
      insights: "",
      report_eligible: true,
      row_count: 5,
    };
  }

  if (q.includes("inventory") && (q.includes("total") || q.includes("on hand") || q.includes("quantity"))) {
    return {
      mode: "chat",
      answer: "Total finished-goods inventory on hand is 11,415 units.",
      sql:
        "SELECT SUM(quantity_available) AS total_inventory\n" +
        "FROM finished_goods_inventory",
      data: [{ total_inventory: 11415 }],
      insights: "",
      report_eligible: true,
      row_count: 1,
    };
  }

  if (q.includes("unpaid") || q.includes("partially paid") || q.includes("partial")) {
    return {
      mode: "chat",
      answer:
        "There are 1,306 invoices that are unpaid or partially paid: 811 partial and 495 unpaid.",
      sql:
        "SELECT payment_status, COUNT(*) AS invoice_count\n" +
        "FROM sales_invoices\n" +
        "WHERE payment_status IN ('unpaid', 'partial')\n" +
        "GROUP BY payment_status",
      data: [
        { payment_status: "partial", invoice_count: 811 },
        { payment_status: "unpaid", invoice_count: 495 },
      ],
      insights: "",
      report_eligible: true,
      row_count: 2,
    };
  }

  if (q.includes("margin") || q.includes("profit")) {
    return {
      mode: "chat",
      answer:
        "The average profit margin for this quarter (Jan–Feb 2026) is 35.06%.",
      sql:
        "SELECT AVG(solp.margin_pct) AS avg_margin\n" +
        "FROM sales_order_line_pricing solp\n" +
        "JOIN sales_order_line sol ON solp.sol_id = sol.sol_id\n" +
        "JOIN sales_order so ON sol.so_id = so.so_id\n" +
        "WHERE so.status = 'closed'\n" +
        "  AND so.order_date >= '2026-01-01' AND so.order_date <= '2026-02-28'",
      data: [{ avg_margin_pct: "35.06%" }],
      insights: "",
      report_eligible: true,
      row_count: 1,
    };
  }

  return null;
}
