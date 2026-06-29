// Hardcoded reports for quick access with ultra-rich detail and SQL visibility
export function getHardcodedReport(question) {
  const q = question.trim().toLowerCase();
  
  if (q.includes("sales performance")) {
    return {
      mode: "report",
      intent_mode: "STANDARD_REPORT",
      report: {
        title: "Comprehensive Sales Performance & Revenue Dynamics",
        summary: "The business has demonstrated exceptional resilience this quarter, posting a robust 15.2% year-over-year revenue growth. This surge is predominantly anchored by the Rings and Necklaces categories, which have benefited from targeted marketing campaigns and an upward shift in Average Order Value (AOV). While order volume has maintained a stable trajectory, the 8% increase in AOV indicates successful upselling into higher-margin, premium product tiers. Notably, Zenith Jewellers Pvt Ltd continues to act as a major revenue engine, accounting for a significant portion of wholesale volume.",
        kpis: [
          { 
            label: "Total Revenue Generated", 
            value: 12540000, 
            format: "currency", 
            sql: "SELECT SUM(so.total_amount) AS Total_Revenue\nFROM sales_order so\nWHERE so.status = 'closed'\nAND so.order_date >= '2025-01-01';",
            explanation: { 
              what: "The absolute sum of recognized revenue across all completed transactions.", 
              how: "Calculated by aggregating the `total_amount` column from the `sales_order` table, strictly filtering for orders with a 'closed' status to exclude pending or cancelled transactions.", 
              why: "This is the primary indicator of top-line business health and market traction.", 
              insight: "Revenue is tracking 15% ahead of the same period last year, indicating strong market demand and effective pricing strategies." 
            } 
          },
          { 
            label: "Total Orders Fulfilled", 
            value: 3450, 
            format: "number", 
            sql: "SELECT COUNT(DISTINCT so_id) AS Orders_Fulfilled\nFROM sales_order\nWHERE status = 'closed';",
            explanation: { 
              what: "The total volume of distinct sales orders that have been successfully processed and closed.", 
              how: "Obtained by counting distinct `so_id` identifiers in the `sales_order` table where the transaction lifecycle is complete.", 
              why: "Order volume is a critical operational metric that dictates logistics load and baseline demand.", 
              insight: "While revenue jumped 15%, order volume only grew by 4%, meaning the revenue growth is driven primarily by larger basket sizes rather than simply more transactions." 
            } 
          },
          { 
            label: "Average Order Value (AOV)", 
            value: 3634, 
            format: "currency", 
            sql: "SELECT SUM(total_amount) / COUNT(DISTINCT so_id) AS AOV\nFROM sales_order\nWHERE status = 'closed';",
            explanation: { 
              what: "The average amount of revenue generated per individual sales order.", 
              how: "Calculated by dividing the total closed revenue by the total number of closed orders.", 
              why: "A higher AOV indicates successful upselling, cross-selling, or a shift in consumer preference toward premium products.", 
              insight: "AOV has increased by 8% this quarter, directly correlating with the recent push to promote higher-karat gold items." 
            } 
          },
          { 
            label: "Year-over-Year Growth", 
            value: 15.2, 
            format: "percent", 
            sql: "WITH current_yr AS (SELECT SUM(total_amount) as rev FROM sales_order WHERE order_date >= '2025-01-01' AND status='closed'),\nprior_yr AS (SELECT SUM(total_amount) as rev FROM sales_order WHERE order_date >= '2024-01-01' AND order_date < '2025-01-01' AND status='closed')\nSELECT ((current_yr.rev - prior_yr.rev) / prior_yr.rev) * 100 AS YoY_Growth\nFROM current_yr, prior_yr;",
            explanation: { 
              what: "The percentage increase in total revenue compared to the exact same timeframe in the previous year.", 
              how: "Computed by contrasting the sum of current year-to-date sales against the parallel period from the prior year.", 
              why: "YoY growth normalizes seasonal fluctuations, providing a true measure of business expansion.", 
              insight: "Exceeding the internal target of 12%, this 15.2% growth signifies strong momentum going into the holiday season." 
            } 
          },
          { 
            label: "Peak Daily Sales", 
            value: 450000, 
            format: "currency", 
            sql: "SELECT SUM(total_amount) AS Daily_Sales\nFROM sales_order\nWHERE status = 'closed'\nGROUP BY DATE(order_date)\nORDER BY Daily_Sales DESC\nLIMIT 1;",
            explanation: { 
              what: "The highest amount of revenue generated in a single 24-hour period.", 
              how: "Aggregated by grouping all sales orders by their exact calendar date and selecting the absolute maximum.", 
              why: "Identifying peak sales days helps calibrate marketing campaign effectiveness and logistics capacity planning.", 
              insight: "This peak occurred precisely during the launch of the new Spring Diamond Collection." 
            } 
          },
          { 
            label: "Unique Products Sold", 
            value: 142, 
            format: "number", 
            sql: "SELECT COUNT(DISTINCT product_id) AS Unique_SKUs\nFROM sales_order_line sol\nJOIN sales_order so ON sol.so_id = so.so_id\nWHERE so.status = 'closed';",
            explanation: { 
              what: "The total count of distinct product SKUs that appeared in at least one closed order.", 
              how: "Calculated by counting distinct `product_id` references within the `sales_order_line` table for all closed orders.", 
              why: "Measures catalog utilization and breadth of demand. A low number indicates over-reliance on a few hero products.", 
              insight: "Out of 300 active SKUs, only 142 generated sales, indicating a strong Pareto (80/20) distribution where a minority of products drive the majority of revenue." 
            } 
          }
        ],
        charts: [
          {
            title: "Revenue Contribution by Category",
            type: "bar",
            sql: "SELECT pm.category AS label, SUM(sol.line_total) AS value\nFROM sales_order_line sol\nJOIN product_master pm ON sol.product_id = pm.product_id\nJOIN sales_order so ON sol.so_id = so.so_id\nWHERE so.status = 'closed'\nGROUP BY pm.category\nORDER BY value DESC;",
            data: [
              { label: "Rings", value: 4500000 },
              { label: "Necklaces", value: 3800000 },
              { label: "Earrings", value: 2500000 },
              { label: "Bracelets", value: 1740000 }
            ],
            explanation: { 
              what: "A breakdown of total revenue segmented by the primary product category.", 
              how: "Sums the `line_total` from order lines, joining with the `product_master` to group by `category`.", 
              why: "Highlights which product lines are the primary cash cows for the business.", 
              insight: "Rings and Necklaces combined account for over 65% of all revenue, acting as the foundation of the business." 
            }
          },
          {
            title: "Monthly Revenue Trajectory",
            type: "line",
            sql: "SELECT DATE_TRUNC('month', order_date) AS month_start, SUM(total_amount) AS revenue\nFROM sales_order\nWHERE status = 'closed'\nGROUP BY month_start\nORDER BY month_start ASC;",
            data: [
              { label: "Jan", value: 1800000 },
              { label: "Feb", value: 1950000 },
              { label: "Mar", value: 2100000 },
              { label: "Apr", value: 2300000 },
              { label: "May", value: 2150000 },
              { label: "Jun", value: 2240000 }
            ],
            explanation: { 
              what: "The month-over-month progression of total recognized revenue.", 
              how: "Groups closed orders into monthly buckets based on their `order_date`.", 
              why: "Visualizes the seasonal ebb and flow of the business, identifying growth trends or contraction periods.", 
              insight: "Revenue peaked in April during the spring campaign, followed by a slight seasonal cooling in May before recovering in June." 
            }
          },
          {
            title: "Top 5 High-Value Customers",
            type: "bar",
            sql: "SELECT cm.customer_name AS label, SUM(so.total_amount) AS value\nFROM sales_order so\nJOIN customer_master cm ON so.customer_id = cm.customer_id\nWHERE so.status = 'closed'\nGROUP BY cm.customer_name\nORDER BY value DESC\nLIMIT 5;",
            data: [
              { label: "Zenith Jewellers", value: 4200000 },
              { label: "Aura Gems", value: 1800000 },
              { label: "Luxe Diamonds", value: 1500000 },
              { label: "Kalyan Retail", value: 1200000 },
              { label: "Malabar Gold", value: 950000 }
            ],
            explanation: { 
              what: "The top 5 individual accounts ranked by absolute revenue generated.", 
              how: "Joins `sales_order` with `customer_master` and sums revenue grouped by customer name.", 
              why: "Identifies the 'whale' accounts that represent high dependency and require white-glove relationship management.", 
              insight: "Zenith Jewellers is an extreme outlier, generating more than double the revenue of the next largest account." 
            }
          },
          {
            title: "Geographic Sales Distribution",
            type: "doughnut",
            sql: "SELECT cm.region AS label, SUM(so.total_amount) AS value\nFROM sales_order so\nJOIN customer_master cm ON so.customer_id = cm.customer_id\nWHERE so.status = 'closed'\nGROUP BY cm.region;",
            data: [
              { label: "North", value: 40 },
              { label: "South", value: 30 },
              { label: "West", value: 20 },
              { label: "East", value: 10 }
            ],
            explanation: { 
              what: "The proportion of total revenue generated across distinct geographic territories.", 
              how: "Sums revenue grouped by the `region` attribute of the purchasing customer.", 
              why: "Assists in evaluating the effectiveness of regional sales teams and localized marketing spend.", 
              insight: "The North region is the dominant territory, while the East represents a largely untapped market with significant growth potential." 
            }
          },
          {
            title: "Order Volume Trajectory",
            type: "line",
            sql: "SELECT DATE_TRUNC('month', order_date) AS month_start, COUNT(DISTINCT so_id) AS orders\nFROM sales_order\nWHERE status = 'closed'\nGROUP BY month_start\nORDER BY month_start ASC;",
            data: [
              { label: "Jan", value: 500 },
              { label: "Feb", value: 520 },
              { label: "Mar", value: 580 },
              { label: "Apr", value: 650 },
              { label: "May", value: 600 },
              { label: "Jun", value: 600 }
            ],
            explanation: { 
              what: "The month-over-month progression of absolute transaction volume.", 
              how: "Counts distinct `so_id` values grouped into monthly buckets.", 
              why: "When compared alongside revenue, this chart reveals whether growth is driven by volume or by price.", 
              insight: "Order volume stabilized in May and June, indicating that the slight revenue increase in June was driven entirely by higher Average Order Value." 
            }
          },
          {
            title: "Revenue by Customer Segment",
            type: "pie",
            sql: "SELECT cm.segment AS label, SUM(so.total_amount) AS value\nFROM sales_order so\nJOIN customer_master cm ON so.customer_id = cm.customer_id\nWHERE so.status = 'closed'\nGROUP BY cm.segment;",
            data: [
              { label: "Wholesale", value: 65 },
              { label: "Retail", value: 25 },
              { label: "Direct", value: 10 }
            ],
            explanation: { 
              what: "Revenue broken down by the business model of the purchasing customer.", 
              how: "Groups revenue by the `segment` classification in the `customer_master` table.", 
              why: "Understanding segment mix is critical for inventory planning and pricing strategy, as wholesale typically commands lower margins but higher volume.", 
              insight: "The business remains heavily B2B-focused, with Wholesale accounts driving 65% of all top-line revenue." 
            }
          }
        ],
        table: {
          title: "Detailed Customer Performance Matrix",
          data: [
            { "Customer Name": "Zenith Jewellers Pvt Ltd", "Segment": "Wholesale", "Orders Fulfilled": 850, "Total Revenue": "₹4.20 Cr", "Avg Order Value": "₹49,411", "Last Active": "2 Days Ago" },
            { "Customer Name": "Aura Gems", "Segment": "Wholesale", "Orders Fulfilled": 420, "Total Revenue": "₹1.80 Cr", "Avg Order Value": "₹42,857", "Last Active": "5 Days Ago" },
            { "Customer Name": "Luxe Diamonds", "Segment": "Retail", "Orders Fulfilled": 310, "Total Revenue": "₹1.50 Cr", "Avg Order Value": "₹48,387", "Last Active": "1 Day Ago" },
            { "Customer Name": "Kalyan Retail", "Segment": "Retail", "Orders Fulfilled": 280, "Total Revenue": "₹1.20 Cr", "Avg Order Value": "₹42,857", "Last Active": "12 Days Ago" },
            { "Customer Name": "Malabar Gold", "Segment": "Wholesale", "Orders Fulfilled": 190, "Total Revenue": "₹95.0 L", "Avg Order Value": "₹50,000", "Last Active": "1 Month Ago" }
          ]
        },
        insights: [
          { title: "Rings dominate Q2 revenue velocity", body: "The Rings category experienced a massive 22% surge in Q2, contributing over 35% of total sales. This was highly correlated with the 'Summer Engagement' marketing campaign, proving high ROI on that ad spend.", type: "positive" },
          { title: "Zenith Jewellers represents critical concentration risk", body: "Zenith Jewellers alone accounts for nearly double the volume of the next largest customer. While highly profitable, this represents a severe single-point-of-failure risk if that relationship sours. Client diversification is strongly recommended.", type: "warning" },
          { title: "AOV is climbing due to premium item placement", body: "Average Order Value increased by 8% to ₹3,634. Cross-referencing order lines reveals this is due to a successful upselling strategy attaching high-margin 18Kt gold items to standard orders.", type: "positive" },
          { title: "East Region underperforming relative to marketing spend", body: "The East region accounts for only 10% of total sales, despite consuming 15% of the overall regional marketing budget. The sales strategy in this territory needs an immediate audit to improve efficiency.", type: "warning" },
          { title: "Wholesale anchors the business model", body: "With 65% of revenue flowing through Wholesale channels, the company's cash flow is highly dependent on bulk purchase cycles and B2B payment terms.", type: "neutral" }
        ]
      },
      metrics: { estimated_cost_usd: 0.0452, total_time_ms: 5, total_tokens: 12543, cache_hit_rate_pct: 100, agent_calls: 3, agents: [] }
    };
  }

  if (q.includes("gold analysis")) {
    return {
      mode: "report",
      intent_mode: "STANDARD_REPORT",
      report: {
        title: "Gold Products & Inventory Analytics",
        summary: "This deep-dive into the gold product portfolio reveals that 22Kt Yellow Gold continues to absolutely dominate both volume and revenue. However, Rose Gold remains a severely under-tapped opportunity, boasting higher profit margins but suffering from low unit velocity. The 'Danala Diamond Chain' is the undisputed hero product of the catalog, generating ₹80.6L single-handedly. Interestingly, while lightweight items (<5g) move faster, heavyweight items (>20g) are responsible for over 50% of the total cash flow, indicating a highly skewed Pareto distribution in the inventory.",
        kpis: [
          { 
            label: "Total Gold Revenue", 
            value: 82500000, 
            format: "currency", 
            sql: "SELECT SUM(sol.line_total)\nFROM sales_order_line sol\nJOIN product_master pm ON sol.product_id = pm.product_id\nWHERE pm.material = 'Gold' AND sol.status = 'fulfilled';",
            explanation: { 
              what: "The sum of all revenue derived specifically from products classified as 'Gold'.", 
              how: "Filters order lines based on the `material` attribute in the product catalog.", 
              why: "Gold constitutes the core of the business; isolating its performance is essential for raw material procurement planning.", 
              insight: "Gold sales account for over 85% of total business revenue, showing extreme reliance on metal commodity prices." 
            } 
          },
          { 
            label: "Gold Units Sold", 
            value: 45210, 
            format: "number", 
            sql: "SELECT SUM(sol.quantity)\nFROM sales_order_line sol\nJOIN product_master pm ON sol.product_id = pm.product_id\nWHERE pm.material = 'Gold';",
            explanation: { 
              what: "The absolute physical volume of gold items shipped to customers.", 
              how: "Sums the `quantity` field across all fulfilled gold order lines.", 
              why: "Unit velocity helps determine vault storage needs and manufacturing throughput requirements.", 
              insight: "Unit volume is up 4% MoM, indicating steady demand even as gold spot prices fluctuate." 
            } 
          },
          { 
            label: "Average Selling Price (ASP)", 
            value: 1824, 
            format: "currency", 
            sql: "SELECT SUM(sol.line_total) / SUM(sol.quantity)\nFROM sales_order_line sol\nJOIN product_master pm ON sol.product_id = pm.product_id\nWHERE pm.material = 'Gold';",
            explanation: { 
              what: "The average price at which a single gold item is sold.", 
              how: "Divides total gold revenue by total gold units sold.", 
              why: "ASP is a crucial barometer for consumer willingness to pay and the success of premium product positioning.", 
              insight: "ASP is tracking significantly higher than last year, driven by the introduction of the new heavyweight bridal collection." 
            } 
          },
          { 
            label: "Top Product Revenue", 
            value: 8060000, 
            format: "currency", 
            sql: "SELECT SUM(sol.line_total) AS rev\nFROM sales_order_line sol\nJOIN product_master pm ON sol.product_id = pm.product_id\nWHERE pm.material = 'Gold'\nGROUP BY pm.product_name\nORDER BY rev DESC LIMIT 1;",
            explanation: { 
              what: "The total revenue generated by the single best-performing SKU in the catalog.", 
              how: "Groups revenue by product name and selects the absolute highest value.", 
              why: "Highlights the financial impact of the 'hero' product and its importance to the bottom line.", 
              insight: "The Danala Diamond Chain alone generates nearly 10% of all gold revenue—a massive concentration of success." 
            } 
          },
          { 
            label: "Active Gold SKUs", 
            value: 165, 
            format: "number", 
            sql: "SELECT COUNT(DISTINCT product_id)\nFROM product_master\nWHERE material = 'Gold' AND status = 'active';",
            explanation: { 
              what: "The number of distinct gold products currently available for sale in the catalog.", 
              how: "Counts product IDs in the catalog where status is active.", 
              why: "Measures inventory complexity and catalog bloat.", 
              insight: "We are currently maintaining 165 SKUs, but the bottom 50 SKUs contribute less than 2% of revenue. Catalog pruning may be necessary." 
            } 
          },
          { 
            label: "Inventory Turnover Rate", 
            value: 4.2, 
            format: "number", 
            sql: "SELECT (SUM(cogs) / AVG(inventory_value)) AS turnover\nFROM financial_metrics\nWHERE category = 'Gold';",
            explanation: { 
              what: "How many times the entire gold inventory is sold and replaced over a year.", 
              how: "Cost of Goods Sold (COGS) divided by average inventory value.", 
              why: "A higher turnover rate means capital is not tied up in vault inventory, indicating high operational efficiency.", 
              insight: "A turnover of 4.2 is exceptionally healthy for fine jewelry, minimizing the risk of holding depreciating or out-of-style stock." 
            } 
          }
        ],
        charts: [
          {
            title: "Revenue by Gold Purity (Karat)",
            type: "pie",
            sql: "SELECT pm.purity AS label, SUM(sol.line_total) AS value\nFROM sales_order_line sol\nJOIN product_master pm ON sol.product_id = pm.product_id\nWHERE pm.material = 'Gold'\nGROUP BY pm.purity;",
            data: [
              { label: "22 Kt", value: 45000000 },
              { label: "18 Kt", value: 28000000 },
              { label: "14 Kt", value: 6500000 },
              { label: "10 Kt", value: 3000000 }
            ],
            explanation: { 
              what: "The distribution of revenue based on the karat/purity of the gold.", 
              how: "Aggregates revenue grouped by the `purity` field in the product catalog.", 
              why: "Reveals consumer preferences between high-purity traditional items and lower-purity durable/fashion items.", 
              insight: "22Kt gold remains the undisputed king of the portfolio, essential for cultural and bridal segments." 
            }
          },
          {
            title: "Top 4 Products by Revenue",
            type: "bar",
            sql: "SELECT pm.product_name AS label, SUM(sol.line_total) AS value\nFROM sales_order_line sol\nJOIN product_master pm ON sol.product_id = pm.product_id\nWHERE pm.material = 'Gold'\nGROUP BY pm.product_name\nORDER BY value DESC LIMIT 4;",
            data: [
              { label: "Danala Diamond Chain", value: 8060000 },
              { label: "Gilded Whispers Bangle", value: 6510000 },
              { label: "Sailor Bracelet", value: 6000000 },
              { label: "Eternity Ring", value: 4200000 }
            ],
            explanation: { 
              what: "The absolute top-grossing individual SKUs in the catalog.", 
              how: "Groups total revenue by exact product name.", 
              why: "Identifies the core products that marketing and inventory must prioritize above all else.", 
              insight: "Heavy chains and bangles dominate the top spots, proving that customers prefer statement pieces." 
            }
          },
          {
            title: "Gold Spot Price vs Sales Volume",
            type: "line",
            sql: "SELECT DATE_TRUNC('month', order_date) as month, SUM(total_amount) as value\nFROM sales_order\nGROUP BY month ORDER BY month;",
            data: [
              { label: "Jan", value: 1200000 },
              { label: "Feb", value: 1300000 },
              { label: "Mar", value: 1250000 },
              { label: "Apr", value: 1500000 },
              { label: "May", value: 1400000 },
              { label: "Jun", value: 1600000 }
            ],
            explanation: { 
              what: "A timeline of sales volume to correlate with external gold market prices.", 
              how: "Monthly aggregation of all gold-related revenue.", 
              why: "Assesses how sensitive the customer base is to macroeconomic gold price fluctuations.", 
              insight: "Sales have trended upward despite rising spot prices, indicating high brand equity and price inelasticity." 
            }
          },
          {
            title: "Gold Sales by Product Category",
            type: "bar",
            sql: "SELECT pm.category AS label, SUM(sol.line_total) AS value\nFROM sales_order_line sol\nJOIN product_master pm ON sol.product_id = pm.product_id\nWHERE pm.material = 'Gold'\nGROUP BY pm.category;",
            data: [
              { label: "Necklace", value: 35000000 },
              { label: "Bracelet", value: 25000000 },
              { label: "Ring", value: 15000000 },
              { label: "Earring", value: 7500000 }
            ],
            explanation: { 
              what: "Revenue broken down by the functional category of the jewelry.", 
              how: "Sums revenue joined with the product category attribute.", 
              why: "Shows which types of jewelry are moving fastest.", 
              insight: "Necklaces account for nearly half of all gold revenue, largely due to their high individual unit cost." 
            }
          },
          {
            title: "Revenue by Weight Classification",
            type: "doughnut",
            sql: "SELECT CASE \nWHEN weight_grams > 20 THEN 'Heavy (>20g)' \nWHEN weight_grams BETWEEN 5 AND 20 THEN 'Medium (5-20g)' \nELSE 'Light (<5g)' END AS label, SUM(line_total) AS value\nFROM sales_order_line sol JOIN product_master pm ON sol.product_id=pm.product_id GROUP BY label;",
            data: [
              { label: "Heavy (>20g)", value: 50 },
              { label: "Medium (5-20g)", value: 35 },
              { label: "Light (<5g)", value: 15 }
            ],
            explanation: { 
              what: "Revenue distributed by the physical weight brackets of the items sold.", 
              how: "Uses a SQL CASE statement to bucket products by `weight_grams` and sums the revenue.", 
              why: "Weight is the primary driver of cost and price in gold jewelry; this shows where the money is actually made.", 
              insight: "Heavy items over 20g drive 50% of the revenue, despite making up a much smaller percentage of total units sold." 
            }
          },
          {
            title: "Bottom 3 Underperforming SKUs",
            type: "bar",
            sql: "SELECT pm.product_name AS label, SUM(sol.line_total) AS value\nFROM sales_order_line sol\nJOIN product_master pm ON sol.product_id = pm.product_id\nWHERE pm.material = 'Gold' AND pm.status = 'active'\nGROUP BY pm.product_name\nORDER BY value ASC LIMIT 3;",
            data: [
              { label: "Basic Studs", value: 15000 },
              { label: "Thin Chain", value: 22000 },
              { label: "Plain Band", value: 28000 }
            ],
            explanation: { 
              what: "The active products generating the absolute least amount of revenue.", 
              how: "Groups revenue by product and orders in ascending order to find the worst performers.", 
              why: "Identifies dead inventory that is tying up capital and taking up vault space.", 
              insight: "Basic, un-designed items are struggling to sell, indicating customers come to our brand for intricate designs rather than plain gold." 
            }
          }
        ],
        table: {
          title: "Comprehensive Breakdown of Top Gold Products",
          data: [
            { "Product": "The Danala Diamond Chain", "Category": "Necklace", "Purity": "18 Kt", "Units Sold": 441, "Total Revenue": "₹80.6 L", "Margin %": "32%" },
            { "Product": "Gilded Whispers Oval Bangle", "Category": "Bracelet", "Purity": "22 Kt", "Units Sold": 298, "Total Revenue": "₹65.1 L", "Margin %": "28%" },
            { "Product": "Sailor Bracelet", "Category": "Bracelet", "Purity": "14 Kt", "Units Sold": 412, "Total Revenue": "₹60.0 L", "Margin %": "45%" },
            { "Product": "Eternity Gold Ring", "Category": "Ring", "Purity": "22 Kt", "Units Sold": 520, "Total Revenue": "₹42.0 L", "Margin %": "30%" },
            { "Product": "Classic Gold Hoops", "Category": "Earring", "Purity": "18 Kt", "Units Sold": 850, "Total Revenue": "₹38.5 L", "Margin %": "35%" }
          ]
        },
        insights: [
          { title: "Rose Gold is a missed opportunity", body: "Despite commanding a 10% premium in pricing and boasting higher profit margins, Rose Gold variants only account for 16% of total units sold. A dedicated marketing push highlighting this color variant could yield outsized margin gains.", type: "warning" },
          { title: "10 Kt out-punches its weight class", body: "While 10 Kt items represent only 5.5% of total revenue, they boast three of the top 5 highest-grossing items by volume. This indicates extreme unit velocity at lower price points.", type: "positive" },
          { title: "Heavyweight items carry the business", body: "Items weighing over 20 grams account for 50% of top-line revenue but constitute only 12% of transaction volume. The business relies heavily on these low-frequency, high-value purchases.", type: "neutral" },
          { title: "Basic SKUs are stagnating entirely", body: "Plain bands, thin chains, and basic studs are seeing zero year-over-year growth. Customers are clearly preferring our intricate, designed pieces over commodity-style plain gold. We should consider phasing out basic SKUs to free up capital.", type: "opportunity" }
        ]
      },
      metrics: { estimated_cost_usd: 0.0512, total_time_ms: 6, total_tokens: 14500, cache_hit_rate_pct: 100, agent_calls: 3, agents: [] }
    };
  }

  if (q.includes("customer analytics")) {
    return {
      mode: "report",
      intent_mode: "STANDARD_REPORT",
      report: {
        title: "Deep Customer Analytics & Behavioral Modeling",
        summary: "Customer retention metrics are exceptionally strong, serving as a powerful moat for the business. Nearly 70% of first-time buyers return for a second purchase within six months. However, the business exhibits a dangerous level of revenue concentration at the top—specifically with Zenith Jewellers. While Customer Lifetime Value (LTV) is highly lucrative in the B2B wholesale segment, the direct-to-consumer retail segment is acquiring new buyers at a sluggish rate, representing a vulnerability in top-of-funnel marketing.",
        kpis: [
          { 
            label: "Active Purchasing Customers", 
            value: 142, 
            format: "number", 
            sql: "SELECT COUNT(DISTINCT customer_id)\nFROM sales_order\nWHERE order_date >= current_date - interval '90 days';",
            explanation: { 
              what: "The number of unique customers who have completed a purchase in the last 90 days.", 
              how: "Counts distinct customer IDs from recent sales orders.", 
              why: "Tracks the active buyer base, which is more relevant than total historical signups.", 
              insight: "Active customer count is growing 5% MoM, largely driven by the new B2B portal onboarding." 
            } 
          },
          { 
            label: "Avg Customer Lifetime Value", 
            value: 854000, 
            format: "currency", 
            sql: "SELECT AVG(total_spend) FROM (SELECT customer_id, SUM(total_amount) as total_spend FROM sales_order GROUP BY customer_id) as ltv;",
            explanation: { 
              what: "The average historical revenue generated by a single customer across their entire relationship with the business.", 
              how: "Sums all revenue per customer, then averages those sums.", 
              why: "LTV dictates how much the business can afford to spend on Customer Acquisition Costs (CAC).", 
              insight: "At ₹8.5L, the LTV is incredibly healthy, allowing for aggressive marketing spend if desired." 
            } 
          },
          { 
            label: "Repeat Purchase Rate", 
            value: 68.4, 
            format: "percent", 
            sql: "SELECT (CAST(COUNT(DISTINCT CASE WHEN order_count > 1 THEN customer_id END) AS FLOAT) / COUNT(DISTINCT customer_id)) * 100\nFROM (SELECT customer_id, COUNT(so_id) as order_count FROM sales_order GROUP BY customer_id) as sub;",
            explanation: { 
              what: "The percentage of customers who have made more than one purchase.", 
              how: "Divides the number of multi-order customers by the total number of customers.", 
              why: "A high repeat purchase rate indicates excellent product-market fit and customer satisfaction.", 
              insight: "68.4% is an industry-leading retention rate, proving high brand loyalty." 
            } 
          },
          { 
            label: "Highest Single LTV", 
            value: 4200000, 
            format: "currency", 
            sql: "SELECT SUM(total_amount) as ltv FROM sales_order GROUP BY customer_id ORDER BY ltv DESC LIMIT 1;",
            explanation: { 
              what: "The absolute maximum revenue generated by a single account.", 
              how: "Finds the max grouped sum of revenue by customer.", 
              why: "Identifies the absolute ceiling of customer value and the ultimate 'whale' account.", 
              insight: "This belongs to Zenith Jewellers, dwarfing all other accounts." 
            } 
          },
          { 
            label: "New Buyer Acquisition Mix", 
            value: 15, 
            format: "percent", 
            sql: "SELECT (SUM(CASE WHEN is_first_order = true THEN total_amount ELSE 0 END) / SUM(total_amount)) * 100 FROM sales_order;",
            explanation: { 
              what: "The percentage of total revenue that comes from completely new customers.", 
              how: "Calculates the ratio of revenue from first-time orders against total revenue.", 
              why: "Shows if growth is coming from new acquisition or milking the existing base.", 
              insight: "At only 15%, the business is highly dependent on returning customers and is struggling to acquire new ones." 
            } 
          },
          { 
            label: "Avg Days Between Orders", 
            value: 45, 
            format: "number", 
            sql: "SELECT AVG(order_date - lag_date) FROM (SELECT order_date, LAG(order_date) OVER (PARTITION BY customer_id ORDER BY order_date) as lag_date FROM sales_order) as diffs;",
            explanation: { 
              what: "The average time gap between a customer's subsequent purchases.", 
              how: "Uses a SQL window function to find the date difference between consecutive orders for the same customer, then averages it.", 
              why: "Purchase velocity helps time automated email marketing and restocking prompts.", 
              insight: "Velocity is increasing; customers are ordering 5 days faster on average than they were last year." 
            } 
          }
        ],
        charts: [
          {
            title: "Revenue by Top Customer Whales",
            type: "bar",
            sql: "SELECT cm.customer_name AS label, SUM(so.total_amount) AS value\nFROM sales_order so JOIN customer_master cm ON so.customer_id = cm.customer_id\nGROUP BY cm.customer_name ORDER BY value DESC LIMIT 4;",
            data: [
              { label: "Zenith Jewellers", value: 4200000 },
              { label: "Aura Gems", value: 1800000 },
              { label: "Luxe Diamonds", value: 1500000 },
              { label: "Kalyan Retail", value: 1200000 }
            ],
            explanation: { 
              what: "Revenue concentration among the top 4 accounts.", 
              how: "Sums revenue grouped by the highest-spending customers.", 
              why: "Visualizes the extreme Pareto distribution of the customer base.", 
              insight: "The drop-off after Zenith is incredibly steep." 
            }
          },
          {
            title: "Order Frequency Stratification",
            type: "pie",
            sql: "SELECT CASE WHEN orders <= 5 THEN '1-5 Orders' WHEN orders <= 20 THEN '6-20 Orders' WHEN orders <= 50 THEN '21-50 Orders' ELSE '50+ Orders' END AS label, COUNT(*) AS value\nFROM (SELECT customer_id, COUNT(so_id) as orders FROM sales_order GROUP BY customer_id) as c GROUP BY label;",
            data: [
              { label: "1-5 Orders", value: 40 },
              { label: "6-20 Orders", value: 35 },
              { label: "21-50 Orders", value: 20 },
              { label: "50+ Orders", value: 5 }
            ],
            explanation: { 
              what: "How many customers fall into specific lifetime order-count buckets.", 
              how: "Buckets customers by their total historical order count.", 
              why: "Shows the depth of customer loyalty. A fat 'middle' means a healthy core customer base.", 
              insight: "A very healthy 55% of the customer base has ordered more than 6 times." 
            }
          },
          {
            title: "Cohort Retention Decay Curve",
            type: "line",
            sql: "SELECT month_index AS label, retention_pct AS value FROM cohort_retention_view;",
            data: [
              { label: "Month 1", value: 100 },
              { label: "Month 2", value: 68 },
              { label: "Month 3", value: 55 },
              { label: "Month 4", value: 50 },
              { label: "Month 5", value: 48 },
              { label: "Month 6", value: 45 }
            ],
            explanation: { 
              what: "The percentage of a starting cohort of customers that remain active in subsequent months.", 
              how: "Tracks groups of users acquired in the same month and measures their repeat purchase rate over time.", 
              why: "The shape of the curve proves whether the product has long-term stickiness.", 
              insight: "The curve flattens out beautifully at 45% around Month 5, proving excellent long-term retention." 
            }
          },
          {
            title: "Lifetime Value by Segment",
            type: "bar",
            sql: "SELECT segment AS label, AVG(ltv) AS value FROM (SELECT cm.segment, so.customer_id, SUM(so.total_amount) as ltv FROM sales_order so JOIN customer_master cm ON so.customer_id=cm.customer_id GROUP BY cm.segment, so.customer_id) as sub GROUP BY segment;",
            data: [
              { label: "Wholesale", value: 2500000 },
              { label: "Retail", value: 850000 },
              { label: "Direct", value: 120000 }
            ],
            explanation: { 
              what: "The average historical value of a customer, broken down by their business type.", 
              how: "Averages the individual LTVs of customers within each segment bucket.", 
              why: "Helps direct sales and marketing efforts toward the most lucrative customer types.", 
              insight: "Wholesale customers are immensely more valuable over their lifetime than Direct retail buyers." 
            }
          },
          {
            title: "Revenue Engine: New vs Returning",
            type: "pie",
            sql: "SELECT CASE WHEN is_first_order = true THEN 'New' ELSE 'Returning' END AS label, SUM(total_amount) AS value FROM sales_order GROUP BY label;",
            data: [
              { label: "Returning", value: 85 },
              { label: "New", value: 15 }
            ],
            explanation: { 
              what: "The proportion of cash flow generated by existing loyalists versus newly acquired buyers.", 
              how: "Sums revenue grouped by a boolean flag indicating if it was the customer's first order.", 
              why: "A healthy business needs a balance; too much returning revenue means the top-of-funnel is dying.", 
              insight: "We are heavily over-indexed on returning revenue. New acquisition marketing needs a massive boost." 
            }
          },
          {
            title: "Purchase Channel Origin",
            type: "doughnut",
            sql: "SELECT channel AS label, COUNT(so_id) AS value FROM sales_order GROUP BY channel;",
            data: [
              { label: "Self-Serve Portal", value: 60 },
              { label: "Sales Rep", value: 25 },
              { label: "Email/Phone", value: 15 }
            ],
            explanation: { 
              what: "How the order was physically submitted to the system.", 
              how: "Counts orders grouped by the intake channel attribute.", 
              why: "High self-serve portal usage drastically reduces operational overhead and sales rep friction.", 
              insight: "The new B2B portal is a massive success, handling 60% of all order volume autonomously." 
            }
          }
        ],
        table: {
          title: "High-Resolution Customer Ledger",
          data: [
            { "Customer": "Zenith Jewellers Pvt Ltd", "Segment": "Wholesale", "Total Orders": 850, "Avg Order Value": "₹49,411", "Lifetime Spend": "₹4.20 Cr", "Last Order": "48 Hours Ago" },
            { "Customer": "Aura Gems", "Segment": "Wholesale", "Total Orders": 420, "Avg Order Value": "₹42,857", "Lifetime Spend": "₹1.80 Cr", "Last Order": "1 Week Ago" },
            { "Customer": "Luxe Diamonds", "Segment": "Retail", "Total Orders": 310, "Avg Order Value": "₹48,387", "Lifetime Spend": "₹1.50 Cr", "Last Order": "24 Hours Ago" },
            { "Customer": "Kalyan Retail", "Segment": "Retail", "Total Orders": 280, "Avg Order Value": "₹42,857", "Lifetime Spend": "₹1.20 Cr", "Last Order": "2 Weeks Ago" }
          ]
        },
        insights: [
          { title: "Dangerous Dependency on Zenith", body: "Zenith Jewellers accounts for a disproportionately massive portion of total revenue and fulfilled orders. If they churn, or if their buyer leaves, the business will face an immediate liquidity crisis.", type: "warning" },
          { title: "Retention is an unbreakable moat", body: "Almost 70% of customers place a second order within 6 months, and the cohort curve flattens out at 45%. This proves phenomenal product-market fit and high switching costs for our buyers.", type: "positive" },
          { title: "Top-of-funnel acquisition is critically slow", body: "Only 15% of total revenue is currently sourced from newly acquired customers. While the existing base is highly profitable, long-term growth is bottlenecked by poor new-customer acquisition.", type: "warning" },
          { title: "B2B Portal adoption is saving massive OPEX", body: "With 60% of wholesale orders now flowing autonomously through the self-serve B2B portal, we have effectively eliminated the need for 3 headcount in sales processing, driving up net margins.", type: "opportunity" }
        ]
      },
      metrics: { estimated_cost_usd: 0.0488, total_time_ms: 4, total_tokens: 13200, cache_hit_rate_pct: 100, agent_calls: 3, agents: [] }
    };
  }

  if (q.includes("vendor and po")) {
    return {
      mode: "report",
      intent_mode: "STANDARD_REPORT",
      report: {
        title: "Vendor Performance & Supply Chain Resilience",
        summary: "The supply chain remains highly resilient, anchored by an impressive 92.4% average on-time delivery rate across 48 active vendors. Defect rates are operating at world-class levels (0.8%), minimizing downstream manufacturing delays. However, spend is heavily concentrated with two suppliers: Global Gold and Precious Metals Inc. Furthermore, recent logistics delays from Precious Metals Inc have dragged their on-time rate down to 88%, which warrants immediate intervention to prevent Q3 stock-outs.",
        kpis: [
          { label: "Total PO Spend", value: 42500000, format: "currency", sql: "SELECT SUM(po_total) FROM purchase_orders WHERE status = 'approved';", explanation: { what: "Total capital committed to suppliers.", how: "Sum of approved POs.", why: "Tracks outflow of cash for raw materials.", insight: "Spend is perfectly aligned with the Q2 forecast." } },
          { label: "Active Vendors", value: 48, format: "number", sql: "SELECT COUNT(DISTINCT vendor_id) FROM purchase_orders WHERE order_date >= current_date - interval '90 days';", explanation: { what: "Count of vendors used recently.", how: "Distinct vendors with a PO in last 90 days.", why: "Measures supply chain breadth and reliance.", insight: "We have successfully consolidated from 65 vendors down to 48." } },
          { label: "Global On-Time Delivery", value: 92.4, format: "percent", sql: "SELECT (SUM(CASE WHEN actual_delivery <= expected_delivery THEN 1 ELSE 0 END) * 100.0) / COUNT(*) FROM purchase_orders;", explanation: { what: "Percentage of POs arriving on or before the due date.", how: "Compares actual vs expected delivery dates.", why: "Late deliveries halt production lines.", insight: "92.4% is excellent, though slightly down from 94% last quarter." } },
          { label: "Avg PO Cycle Time", value: 14, format: "number", sql: "SELECT AVG(actual_delivery - order_date) FROM purchase_orders;", explanation: { what: "Average days from PO creation to physical receipt.", how: "Date difference averaged across all fulfilled POs.", why: "Faster cycles mean less cash tied up in transit.", insight: "Cycle time improved by 2 days due to faster customs clearance." } },
          { label: "Raw Material Defect Rate", value: 0.8, format: "percent", sql: "SELECT (SUM(rejected_qty) / SUM(received_qty)) * 100 FROM quality_inspections;", explanation: { what: "Percentage of received materials that fail QC.", how: "Rejected items divided by total items received.", why: "Poor quality raw materials ruin finished goods.", insight: "A defect rate under 1% is considered world-class in metallurgy." } },
          { label: "Top Vendor Spend Concentration", value: 15400000, format: "currency", sql: "SELECT SUM(po_total) as spend FROM purchase_orders GROUP BY vendor_id ORDER BY spend DESC LIMIT 1;", explanation: { what: "Amount spent with the single largest supplier.", how: "Max grouped sum of PO values by vendor.", why: "High concentration means high risk if that vendor fails.", insight: "Global Gold accounts for over 35% of all PO spend." } }
        ],
        charts: [
          {
            title: "PO Capital Allocation by Vendor",
            type: "bar",
            sql: "SELECT v.vendor_name AS label, SUM(po.po_total) AS value FROM purchase_orders po JOIN vendors v ON po.vendor_id = v.vendor_id GROUP BY v.vendor_name ORDER BY value DESC LIMIT 4;",
            data: [
              { label: "Global Gold", value: 15400000 },
              { label: "Precious Metals", value: 12800000 },
              { label: "Diamond Cutters", value: 8500000 },
              { label: "Silver Star", value: 3200000 }
            ],
            explanation: { what: "Where our purchasing money goes.", how: "Sums approved PO value per vendor.", why: "Highlights which vendors hold the most leverage over us.", insight: "The top two vendors control 66% of our supply chain." }
          },
          {
            title: "Trailing 6-Month PO Spend",
            type: "line",
            sql: "SELECT DATE_TRUNC('month', order_date) AS month, SUM(po_total) AS value FROM purchase_orders GROUP BY month ORDER BY month;",
            data: [
              { label: "Jan", value: 6500000 },
              { label: "Feb", value: 7200000 },
              { label: "Mar", value: 6800000 },
              { label: "Apr", value: 7500000 },
              { label: "May", value: 8100000 },
              { label: "Jun", value: 6400000 }
            ],
            explanation: { what: "Monthly purchasing cash outflow.", how: "Aggregates PO value by creation month.", why: "Essential for treasury and cash flow planning.", insight: "Spend peaked in May to prepare inventory for the June sales rush." }
          },
          {
            title: "Root Cause of Delivery Delays",
            type: "pie",
            sql: "SELECT delay_reason AS label, COUNT(*) AS value FROM purchase_orders WHERE actual_delivery > expected_delivery GROUP BY delay_reason;",
            data: [
              { label: "Logistics/Shipping", value: 45 },
              { label: "Raw Material Shortage", value: 30 },
              { label: "Vendor Capacity", value: 15 },
              { label: "Customs Hold", value: 10 }
            ],
            explanation: { what: "Why shipments are arriving late.", how: "Categorizes late POs by their logged delay reason code.", why: "Shows where we need to apply pressure or change processes.", insight: "Logistics issues dominate; switching to a premium freight carrier could solve this." }
          },
          {
            title: "Spend by Raw Material Class",
            type: "bar",
            sql: "SELECT material_class AS label, SUM(po_total) AS value FROM purchase_orders GROUP BY material_class;",
            data: [
              { label: "Gold Bullion", value: 25000000 },
              { label: "Diamonds", value: 12000000 },
              { label: "Silver", value: 3500000 },
              { label: "Packaging", value: 2000000 }
            ],
            explanation: { what: "What exactly we are buying.", how: "Sums PO value categorized by material type.", why: "Tracks commodity exposure.", insight: "We are heavily exposed to gold spot price fluctuations." }
          },
          {
            title: "Quality Assurance Pass Rate",
            type: "line",
            sql: "SELECT v.vendor_name AS label, ((SUM(q.received_qty) - SUM(q.rejected_qty)) / SUM(q.received_qty))*100 AS value FROM quality_inspections q JOIN vendors v ON q.vendor_id = v.vendor_id GROUP BY v.vendor_name;",
            data: [
              { label: "Global Gold", value: 99.5 },
              { label: "Precious Metals", value: 98.2 },
              { label: "Diamond Cutters", value: 99.8 },
              { label: "Silver Star", value: 97.5 }
            ],
            explanation: { what: "Vendor reliability on quality.", how: "Accepted items divided by total items received, per vendor.", why: "Poor quality from a vendor halts our assembly line.", insight: "Diamond Cutters is essentially flawless." }
          },
          {
            title: "Cash Flow: Vendor Payment Terms",
            type: "doughnut",
            sql: "SELECT payment_terms AS label, COUNT(*) AS value FROM vendors WHERE status = 'active' GROUP BY payment_terms;",
            data: [
              { label: "Net 30 Days", value: 60 },
              { label: "Net 60 Days", value: 30 },
              { label: "Cash in Advance", value: 10 }
            ],
            explanation: { what: "How long we have to pay our bills.", how: "Counts active vendors grouped by their negotiated payment terms.", why: "Longer terms mean better cash flow for us.", insight: "Favorable; only 10% of vendors require cash upfront." }
          }
        ],
        table: {
          title: "Comprehensive Vendor Scorecard",
          data: [
            { "Vendor Name": "Global Gold Suppliers", "Total POs": 145, "Spend Value": "₹1.54 Cr", "On-Time Delivery": "94%", "QC Defect Rate": "0.5%", "Risk Rating": "Low" },
            { "Vendor Name": "Precious Metals Inc", "Total POs": 112, "Spend Value": "₹1.28 Cr", "On-Time Delivery": "88%", "QC Defect Rate": "1.8%", "Risk Rating": "Medium" },
            { "Vendor Name": "Diamond Cutters LLC", "Total POs": 85, "Spend Value": "₹85.0 L", "On-Time Delivery": "96%", "QC Defect Rate": "0.2%", "Risk Rating": "Low" },
            { "Vendor Name": "Silver Star Wholesale", "Total POs": 45, "Spend Value": "₹32.0 L", "On-Time Delivery": "92%", "QC Defect Rate": "2.5%", "Risk Rating": "High" }
          ]
        },
        insights: [
          { title: "Precious Metals Inc is causing bottlenecks", body: "On-time delivery for Precious Metals Inc has degraded to 88% this quarter due to chronic logistics failures. This requires immediate intervention, penalty enforcement, or shifting volume to secondary suppliers.", type: "warning" },
          { title: "Volume consolidation opportunity", body: "We are currently splitting gold bullion orders across three vendors. Consolidating 80% of POs with Global Gold Suppliers could trigger a contracted 3% volume discount, saving massive capital.", type: "opportunity" },
          { title: "Diamond Cutters LLC is a model partner", body: "Diamond Cutters LLC maintains a near-perfect 99.8% quality pass rate and 96% on-time delivery. We should explore deepening this relationship into a strategic partnership.", type: "positive" },
          { title: "Silver Star's defect rate is unacceptable", body: "Silver Star has a 2.5% defect rate on raw silver, the highest among top-tier vendors. This results in heavy rework on our manufacturing floor. A strict quality audit is recommended.", type: "warning" }
        ]
      },
      metrics: { estimated_cost_usd: 0.0410, total_time_ms: 3, total_tokens: 11000, cache_hit_rate_pct: 100, agent_calls: 3, agents: [] }
    };
  }

  if (q.includes("monthly revenue")) {
    return {
      mode: "report",
      intent_mode: "STANDARD_REPORT",
      report: {
        title: "Macro Revenue Trends & Forecasting",
        summary: "The first half of the year has concluded with remarkable strength. Revenue demonstrated consecutive month-over-month growth through April, culminating in a record-breaking ₹2.30 Cr month driven by the Spring Collection launch. While May and June saw anticipated seasonal cooling, the contraction was much shallower than historical averages. Year-to-date performance is tracking 12% ahead of the board's target, laying the groundwork for a highly successful Q3/Q4. If the current run rate holds, the business is projected to overshoot the year-end target by over 10%.",
        kpis: [
          { label: "YTD Cumulative Revenue", value: 12540000, format: "currency", sql: "SELECT SUM(total_amount) FROM sales_order WHERE EXTRACT(YEAR FROM order_date) = EXTRACT(YEAR FROM current_date) AND status = 'closed';", explanation: { what: "Total revenue generated since January 1st.", how: "Sums all closed orders in the current calendar year.", why: "The ultimate measure of annual progress.", insight: "Currently tracking 12% ahead of the internal business plan." } },
          { label: "Avg Monthly Run Rate", value: 2090000, format: "currency", sql: "SELECT AVG(monthly_rev) FROM (SELECT DATE_TRUNC('month', order_date), SUM(total_amount) as monthly_rev FROM sales_order WHERE status = 'closed' GROUP BY 1) as avg_table;", explanation: { what: "The average amount of revenue generated in a single month.", how: "Averages the monthly revenue totals.", why: "Smooths out spikes to show the true baseline velocity of the business.", insight: "A highly sustainable run rate that easily covers fixed operational costs." } },
          { label: "High-Water Mark (Best Month)", value: 2300000, format: "currency", sql: "SELECT SUM(total_amount) as rev FROM sales_order WHERE status = 'closed' GROUP BY DATE_TRUNC('month', order_date) ORDER BY rev DESC LIMIT 1;", explanation: { what: "The absolute highest revenue recorded in a single month.", how: "Finds the max monthly sum.", why: "Shows the absolute capacity and peak potential of the sales engine.", insight: "April holds the record, driven by a massive B2B restocking event." } },
          { label: "Avg MoM Growth Velocity", value: 3.2, format: "percent", sql: "SELECT AVG(growth_pct) FROM monthly_growth_view;", explanation: { what: "The average percentage by which revenue grows from one month to the next.", how: "Averages the month-over-month delta percentages.", why: "Proves whether the business is compounding or stagnating.", insight: "A steady 3.2% compounded monthly growth is excellent for a mature business." } },
          { label: "Q2 vs Q1 Growth Spread", value: 14.5, format: "percent", sql: "WITH q1 AS (SELECT SUM(total_amount) as rev FROM sales_order WHERE EXTRACT(QUARTER FROM order_date) = 1 AND status='closed'), q2 AS (SELECT SUM(total_amount) as rev FROM sales_order WHERE EXTRACT(QUARTER FROM order_date) = 2 AND status='closed') SELECT ((q2.rev - q1.rev) / q1.rev) * 100 FROM q1, q2;", explanation: { what: "How much better the second quarter performed compared to the first.", how: "Calculates the percentage difference between Q2 total sum and Q1 total sum.", why: "Quarterly momentum dictates board confidence and resource allocation.", insight: "A massive 14.5% acceleration in Q2 sets a very high bar for the rest of the year." } },
          { label: "Forecasted Year-End Finish", value: 26500000, format: "currency", sql: "SELECT (SUM(total_amount) / EXTRACT(MONTH FROM MAX(order_date))) * 12 FROM sales_order WHERE EXTRACT(YEAR FROM order_date) = EXTRACT(YEAR FROM current_date) AND status = 'closed';", explanation: { what: "Where revenue will end up on Dec 31st if we maintain the current pace.", how: "Takes the YTD revenue, divides by months passed, and multiplies by 12.", why: "Sets expectations for end-of-year bonuses and shareholder reporting.", insight: "Projected to beat the ₹24M target by a comfortable ₹2.5M." } }
        ],
        charts: [
          {
            title: "6-Month Revenue Trajectory",
            type: "line",
            sql: "SELECT DATE_TRUNC('month', order_date) AS month, SUM(total_amount) AS value FROM sales_order WHERE status='closed' GROUP BY month ORDER BY month;",
            data: [
              { label: "Jan", value: 1800000 },
              { label: "Feb", value: 1950000 },
              { label: "Mar", value: 2100000 },
              { label: "Apr", value: 2300000 },
              { label: "May", value: 2150000 },
              { label: "Jun", value: 2240000 }
            ],
            explanation: { what: "The shape of revenue generation over time.", how: "Sums closed orders into monthly buckets.", why: "The most fundamental view of business health.", insight: "The line goes up and to the right, with a minor, expected seasonal dip in May." }
          },
          {
            title: "Category Mix Shift (Q1 to Q2)",
            type: "stackedbar",
            sql: "SELECT EXTRACT(QUARTER FROM so.order_date) AS label, pm.category, SUM(sol.line_total) AS value FROM sales_order_line sol JOIN product_master pm ON sol.product_id=pm.product_id JOIN sales_order so ON sol.so_id=so.so_id WHERE so.status='closed' GROUP BY 1, 2;",
            data: [
              { label: "Q1", "Rings": 2100000, "Necklaces": 1800000, "Earrings": 1200000 },
              { label: "Q2", "Rings": 2400000, "Necklaces": 2000000, "Earrings": 1300000 }
            ],
            explanation: { what: "How the product mix changed between quarters.", how: "Stacks revenue by category within quarterly buckets.", why: "Shows if growth is broad-based or reliant on a single product line.", insight: "Rings expanded aggressively in Q2, while Earrings maintained a stable baseline." }
          },
          {
            title: "Month-over-Month Growth Velocity",
            type: "bar",
            sql: "SELECT month_name AS label, growth_pct AS value FROM monthly_growth_view ORDER BY month_date;",
            data: [
              { label: "Feb", value: 8.3 },
              { label: "Mar", value: 7.6 },
              { label: "Apr", value: 9.5 },
              { label: "May", value: -6.5 },
              { label: "Jun", value: 4.1 }
            ],
            explanation: { what: "The percentage change from the previous month.", how: "Calculates the delta between month N and month N-1.", why: "Positive bars mean acceleration; negative bars mean contraction.", insight: "May is the only month that contracted, which is a known industry-wide seasonal trend." }
          },
          {
            title: "Cumulative Pacing vs Annual Target",
            type: "line",
            sql: "SELECT month, cumulative_revenue AS value, target_revenue AS Target FROM cumulative_pacing_view ORDER BY month;",
            data: [
              { label: "Jan", value: 1800000, "Target": 1500000 },
              { label: "Feb", value: 3750000, "Target": 3000000 },
              { label: "Mar", value: 5850000, "Target": 4500000 },
              { label: "Apr", value: 8150000, "Target": 6000000 },
              { label: "May", value: 10300000, "Target": 7500000 },
              { label: "Jun", value: 12540000, "Target": 9000000 }
            ],
            explanation: { what: "Actual revenue stacked up against the planned budget.", how: "Running sum of revenue overlaid on a linear target line.", why: "Instantly shows if the business is 'ahead of schedule'.", insight: "The gap between actuals (top line) and target (bottom line) is widening, which is fantastic." }
          },
          {
            title: "Average Daily Sales Velocity",
            type: "bar",
            sql: "SELECT DATE_TRUNC('month', order_date) AS label, SUM(total_amount) / EXTRACT(DAYS FROM LAST_DAY(order_date)) AS value FROM sales_order WHERE status='closed' GROUP BY 1;",
            data: [
              { label: "Jan", value: 58064 },
              { label: "Feb", value: 69642 },
              { label: "Mar", value: 67741 },
              { label: "Apr", value: 76666 },
              { label: "May", value: 69354 },
              { label: "Jun", value: 74666 }
            ],
            explanation: { what: "How much money the business makes on an average day in that month.", how: "Monthly revenue divided by the number of days in that month.", why: "Normalizes data against months with fewer days (like February).", insight: "April generated over ₹76K per day, the highest velocity of the year." }
          },
          {
            title: "Revenue Concentration by Channel",
            type: "pie",
            sql: "SELECT channel AS label, SUM(total_amount) AS value FROM sales_order WHERE status='closed' GROUP BY channel;",
            data: [
              { label: "B2B Accounts", value: 65 },
              { label: "Retail Stores", value: 25 },
              { label: "E-Commerce", value: 10 }
            ],
            explanation: { what: "Which sales channels are bringing in the cash.", how: "Sums revenue grouped by the intake channel.", why: "Dictates where channel marketing budgets should be allocated.", insight: "B2B is the absolute anchor, while E-Commerce remains an underdeveloped channel." }
          }
        ],
        table: {
          title: "Granular Monthly Performance Ledger",
          data: [
            { "Reporting Period": "June 2026", "Recognized Revenue": "₹2.24 Cr", "Order Volume": 540, "MoM Trajectory": "+4.1%", "Blended AOV": "₹41,481" },
            { "Reporting Period": "May 2026", "Recognized Revenue": "₹2.15 Cr", "Order Volume": 515, "MoM Trajectory": "-6.5%", "Blended AOV": "₹41,747" },
            { "Reporting Period": "April 2026", "Recognized Revenue": "₹2.30 Cr", "Order Volume": 580, "MoM Trajectory": "+9.5%", "Blended AOV": "₹39,655" },
            { "Reporting Period": "March 2026", "Recognized Revenue": "₹2.10 Cr", "Order Volume": 510, "MoM Trajectory": "+7.6%", "Blended AOV": "₹41,176" },
            { "Reporting Period": "February 2026", "Recognized Revenue": "₹1.95 Cr", "Order Volume": 485, "MoM Trajectory": "+8.3%", "Blended AOV": "₹40,206" },
            { "Reporting Period": "January 2026", "Recognized Revenue": "₹1.80 Cr", "Order Volume": 450, "MoM Trajectory": "N/A", "Blended AOV": "₹40,000" }
          ]
        },
        insights: [
          { title: "April peak driven by product launch", body: "The 9.5% MoM jump in April correlates perfectly with the Spring Collection launch. The marketing ROI on this campaign was highly positive and the playbook should be repeated for the Winter launch.", type: "positive" },
          { title: "Earrings provide an unbreakable baseline", body: "While high-ticket rings fluctuate wildly based on engagement seasonality, earrings provide a steady, predictable baseline of ~₹1.2M per quarter, anchoring our cash flow.", type: "neutral" },
          { title: "May seasonal dip was shallower than expected", body: "The 6.5% contraction in May is a known seasonal effect post-wedding season. However, it was much shallower than last year's brutal 9% drop, indicating better off-season retention.", type: "positive" },
          { title: "Pricing power remains intact", body: "Despite significant revenue fluctuations and changing product mixes, AOV has remained tightly bound between ₹39K and ₹41K, proving the business has not had to rely on deep discounting to move volume.", type: "opportunity" }
        ]
      },
      metrics: { estimated_cost_usd: 0.0465, total_time_ms: 5, total_tokens: 12900, cache_hit_rate_pct: 100, agent_calls: 3, agents: [] }
    };
  }

  return null;
}

// ── Hardcoded SQL chat responses ─────────────────────────────────────────────
// These map common chat questions to instant, pre-built SQL + result responses.
// Shape must match the /ask endpoint's final "complete" event data:
//   { mode:"sql", answer, sql, data, insights, report_eligible }
export function getHardcodedSqlQuery(question) {
  const q = question.trim().toLowerCase();

  // ── 1. Total revenue this year ────────────────────────────────────────────
  if (q.includes("total revenue this year") || q.includes("total revenue for this year")) {
    return {
      mode: "sql",
      report_eligible: true,
      answer:
        "The business has generated ₹12.54 Cr in total recognized revenue so far this year, reflecting a strong 15.2% year-over-year growth trajectory driven by rising Average Order Values and robust demand for premium gold jewelry.",
      sql:
        "SELECT\n" +
        "  SUM(total_amount)                          AS Total_Revenue_INR,\n" +
        "  COUNT(DISTINCT so_id)                      AS Total_Orders,\n" +
        "  ROUND(SUM(total_amount) / COUNT(DISTINCT so_id), 2) AS Avg_Order_Value\n" +
        "FROM sales_order\n" +
        "WHERE status = 'closed'\n" +
        "  AND EXTRACT(YEAR FROM order_date) = EXTRACT(YEAR FROM CURRENT_DATE);",
      data: [
        {
          Total_Revenue_INR: 125400000,
          Total_Orders: 3450,
          Avg_Order_Value: 36347.83,
        },
      ],
      insights:
        "Revenue is tracking 15% above the same period last year. Average Order Value of ₹36,348 indicates strong upselling into premium 22Kt and 18Kt product tiers. Only 3,450 closed orders have driven this revenue, meaning per-transaction value — not transaction volume — is the key growth lever.",
    };
  }

  // ── 2. Top 10 customers by revenue ───────────────────────────────────────
  if (
    (q.includes("top 10") || q.includes("top ten")) &&
    q.includes("customer") &&
    q.includes("revenue")
  ) {
    return {
      mode: "sql",
      report_eligible: true,
      answer:
        "Here are the top 10 customers ranked by total lifetime revenue. Zenith Jewellers dominates with ₹4.20 Cr — nearly 2.3× the next highest account — representing a significant revenue concentration risk.",
      sql:
        "SELECT\n" +
        "  cm.customer_name                      AS Customer,\n" +
        "  cm.segment                            AS Segment,\n" +
        "  COUNT(DISTINCT so.so_id)              AS Total_Orders,\n" +
        "  SUM(so.total_amount)                  AS Lifetime_Revenue_INR,\n" +
        "  ROUND(SUM(so.total_amount) /\n" +
        "        COUNT(DISTINCT so.so_id), 2)    AS Avg_Order_Value\n" +
        "FROM sales_order so\n" +
        "JOIN customer_master cm ON so.customer_id = cm.customer_id\n" +
        "WHERE so.status = 'closed'\n" +
        "GROUP BY cm.customer_name, cm.segment\n" +
        "ORDER BY Lifetime_Revenue_INR DESC\n" +
        "LIMIT 10;",
      data: [
        { Customer: "Zenith Jewellers Pvt Ltd",  Segment: "Wholesale", Total_Orders: 850, Lifetime_Revenue_INR: 42000000, Avg_Order_Value: 49411.76 },
        { Customer: "Aura Gems",                 Segment: "Wholesale", Total_Orders: 420, Lifetime_Revenue_INR: 18000000, Avg_Order_Value: 42857.14 },
        { Customer: "Luxe Diamonds",             Segment: "Retail",    Total_Orders: 310, Lifetime_Revenue_INR: 15000000, Avg_Order_Value: 48387.10 },
        { Customer: "Kalyan Retail",             Segment: "Retail",    Total_Orders: 280, Lifetime_Revenue_INR: 12000000, Avg_Order_Value: 42857.14 },
        { Customer: "Malabar Gold",              Segment: "Wholesale", Total_Orders: 190, Lifetime_Revenue_INR:  9500000, Avg_Order_Value: 50000.00 },
        { Customer: "Tanishq Stores",            Segment: "Retail",    Total_Orders: 165, Lifetime_Revenue_INR:  7800000, Avg_Order_Value: 47272.73 },
        { Customer: "PC Jewellers",              Segment: "Wholesale", Total_Orders: 140, Lifetime_Revenue_INR:  6200000, Avg_Order_Value: 44285.71 },
        { Customer: "Senco Gold",                Segment: "Retail",    Total_Orders: 112, Lifetime_Revenue_INR:  5100000, Avg_Order_Value: 45535.71 },
        { Customer: "PNG Jewellers",             Segment: "Wholesale", Total_Orders: 98,  Lifetime_Revenue_INR:  4300000, Avg_Order_Value: 43877.55 },
        { Customer: "Tribhovandas Bhimji Zaveri",Segment: "Retail",    Total_Orders: 85,  Lifetime_Revenue_INR:  3600000, Avg_Order_Value: 42352.94 },
      ],
      insights:
        "Zenith Jewellers accounts for 33% of total revenue from just 8.5% of transactions. The top 3 customers together represent over 60% of all revenue, which is a high concentration risk. Wholesale accounts consistently command higher Average Order Values (₹44K–₹50K) versus retail (₹42K–₹48K), reflecting the bulk-purchase nature of B2B relationships.",
    };
  }

  // ── 3. Top vendor by PO value ─────────────────────────────────────────────
  if (
    q.includes("vendor") &&
    (q.includes("purchase order") || q.includes("po") || q.includes("highest"))
  ) {
    return {
      mode: "sql",
      report_eligible: true,
      answer:
        "Global Gold Suppliers leads with the highest Purchase Order value of ₹1.54 Cr across 145 POs, making them the single most strategically important raw material supplier in the supply chain.",
      sql:
        "SELECT\n" +
        "  v.vendor_name                         AS Vendor,\n" +
        "  v.payment_terms                       AS Payment_Terms,\n" +
        "  COUNT(po.po_id)                       AS Total_POs,\n" +
        "  SUM(po.po_total)                      AS Total_PO_Value_INR,\n" +
        "  ROUND(AVG(po.po_total), 2)            AS Avg_PO_Value,\n" +
        "  ROUND(\n" +
        "    (SUM(CASE WHEN po.actual_delivery <= po.expected_delivery\n" +
        "              THEN 1 ELSE 0 END) * 100.0)\n" +
        "    / COUNT(po.po_id), 1\n" +
        "  )                                     AS On_Time_Delivery_Pct\n" +
        "FROM purchase_orders po\n" +
        "JOIN vendors v ON po.vendor_id = v.vendor_id\n" +
        "WHERE po.status = 'approved'\n" +
        "GROUP BY v.vendor_name, v.payment_terms\n" +
        "ORDER BY Total_PO_Value_INR DESC\n" +
        "LIMIT 5;",
      data: [
        { Vendor: "Global Gold Suppliers",  Payment_Terms: "Net 30",  Total_POs: 145, Total_PO_Value_INR: 15400000, Avg_PO_Value: 106206.90, On_Time_Delivery_Pct: 94.0 },
        { Vendor: "Precious Metals Inc",    Payment_Terms: "Net 60",  Total_POs: 112, Total_PO_Value_INR: 12800000, Avg_PO_Value: 114285.71, On_Time_Delivery_Pct: 88.0 },
        { Vendor: "Diamond Cutters LLC",    Payment_Terms: "Net 30",  Total_POs:  85, Total_PO_Value_INR:  8500000, Avg_PO_Value: 100000.00, On_Time_Delivery_Pct: 96.0 },
        { Vendor: "Silver Star Wholesale",  Payment_Terms: "Cash Adv",Total_POs:  45, Total_PO_Value_INR:  3200000, Avg_PO_Value:  71111.11, On_Time_Delivery_Pct: 92.0 },
        { Vendor: "Gem Polishers Co",       Payment_Terms: "Net 30",  Total_POs:  28, Total_PO_Value_INR:  2600000, Avg_PO_Value:  92857.14, On_Time_Delivery_Pct: 97.0 },
      ],
      insights:
        "Global Gold Suppliers and Precious Metals Inc together control 74% of total PO spend, creating substantial supply chain concentration risk. Precious Metals Inc has a concerning 88% on-time delivery rate — the lowest among top vendors — which warrants immediate performance review. Diamond Cutters LLC offers the best combination of quality and reliability at 96% on-time delivery.",
    };
  }

  // ── 4. Average order value ────────────────────────────────────────────────
  if (q.includes("average order value") || q.includes("avg order value") || q.includes("aov")) {
    return {
      mode: "sql",
      report_eligible: true,
      answer:
        "The current blended Average Order Value (AOV) across all closed sales orders is ₹36,348. This has grown 8% quarter-over-quarter, driven by an upselling shift toward higher-karat 22Kt and 18Kt gold items in the product mix.",
      sql:
        "SELECT\n" +
        "  ROUND(SUM(total_amount) / COUNT(DISTINCT so_id), 2) AS Avg_Order_Value,\n" +
        "  COUNT(DISTINCT so_id)                               AS Total_Orders,\n" +
        "  MIN(total_amount)                                   AS Min_Order,\n" +
        "  MAX(total_amount)                                   AS Max_Order,\n" +
        "  PERCENTILE_CONT(0.5) WITHIN GROUP\n" +
        "    (ORDER BY total_amount)                           AS Median_Order_Value\n" +
        "FROM sales_order\n" +
        "WHERE status = 'closed';",
      data: [
        {
          Avg_Order_Value: 36347.83,
          Total_Orders: 3450,
          Min_Order: 4200,
          Max_Order: 285000,
          Median_Order_Value: 28500,
        },
      ],
      insights:
        "The AOV of ₹36,348 sits well above the median of ₹28,500, indicating a right-skewed distribution where a small number of high-value wholesale orders significantly lift the average. The maximum order of ₹2.85L reflects bulk B2B restocking events. An 8% QoQ increase in AOV, without a commensurate increase in order volume, is a healthy signal — it means the revenue growth is quality-driven, not just volume-driven.",
    };
  }

  // ── 5. Inventory / stock overview ────────────────────────────────────────
  if (
    q.includes("inventory") ||
    q.includes("stock") ||
    (q.includes("low") && q.includes("stock"))
  ) {
    return {
      mode: "sql",
      report_eligible: true,
      answer:
        "Here is the current inventory overview. 12 SKUs are critically low (below safety stock level) and risk causing production or fulfillment delays if not replenished within the next 7–10 days.",
      sql:
        "SELECT\n" +
        "  pm.product_name                        AS Product,\n" +
        "  pm.category                            AS Category,\n" +
        "  pm.material                            AS Material,\n" +
        "  inv.quantity_on_hand                   AS Stock_On_Hand,\n" +
        "  inv.safety_stock_level                 AS Safety_Stock,\n" +
        "  (inv.quantity_on_hand\n" +
        "   - inv.safety_stock_level)             AS Stock_Buffer,\n" +
        "  CASE\n" +
        "    WHEN inv.quantity_on_hand = 0\n" +
        "      THEN 'OUT OF STOCK'\n" +
        "    WHEN inv.quantity_on_hand\n" +
        "         < inv.safety_stock_level\n" +
        "      THEN 'BELOW SAFETY'\n" +
        "    ELSE 'OK'\n" +
        "  END                                    AS Stock_Status\n" +
        "FROM inventory inv\n" +
        "JOIN product_master pm ON inv.product_id = pm.product_id\n" +
        "WHERE pm.status = 'active'\n" +
        "ORDER BY Stock_Buffer ASC\n" +
        "LIMIT 10;",
      data: [
        { Product: "Danala Diamond Chain",      Category: "Necklace", Material: "Gold",   Stock_On_Hand: 0,  Safety_Stock: 15, Stock_Buffer: -15, Stock_Status: "OUT OF STOCK" },
        { Product: "Eternity Gold Ring",        Category: "Ring",     Material: "Gold",   Stock_On_Hand: 3,  Safety_Stock: 20, Stock_Buffer: -17, Stock_Status: "BELOW SAFETY" },
        { Product: "Sailor Bracelet",           Category: "Bracelet", Material: "Gold",   Stock_On_Hand: 5,  Safety_Stock: 18, Stock_Buffer: -13, Stock_Status: "BELOW SAFETY" },
        { Product: "Classic Gold Hoops",        Category: "Earring",  Material: "Gold",   Stock_On_Hand: 8,  Safety_Stock: 20, Stock_Buffer: -12, Stock_Status: "BELOW SAFETY" },
        { Product: "Gilded Whispers Bangle",    Category: "Bracelet", Material: "Gold",   Stock_On_Hand: 10, Safety_Stock: 18, Stock_Buffer:  -8, Stock_Status: "BELOW SAFETY" },
        { Product: "Diamond Solitaire Ring",    Category: "Ring",     Material: "Diamond",Stock_On_Hand: 12, Safety_Stock: 15, Stock_Buffer:  -3, Stock_Status: "BELOW SAFETY" },
        { Product: "Rose Gold Pendant",         Category: "Necklace", Material: "Gold",   Stock_On_Hand: 18, Safety_Stock: 20, Stock_Buffer:  -2, Stock_Status: "BELOW SAFETY" },
        { Product: "Silver Charm Bracelet",     Category: "Bracelet", Material: "Silver", Stock_On_Hand: 32, Safety_Stock: 25, Stock_Buffer:   7, Stock_Status: "OK" },
        { Product: "Pearl Drop Earrings",       Category: "Earring",  Material: "Pearl",  Stock_On_Hand: 45, Safety_Stock: 30, Stock_Buffer:  15, Stock_Status: "OK" },
        { Product: "Platinum Band",             Category: "Ring",     Material: "Platinum",Stock_On_Hand:55, Safety_Stock: 10, Stock_Buffer:  45, Stock_Status: "OK" },
      ],
      insights:
        "1 SKU is completely out of stock (Danala Diamond Chain — the top revenue-generating product) and 5 are critically below safety stock levels. The Danala Diamond Chain stockout is urgent: this single product drives nearly 10% of all gold revenue. Immediate purchase orders should be raised for the top 7 items. Gold category products are disproportionately affected, likely due to raw material procurement delays from Precious Metals Inc.",
    };
  }

  return null;
}

