-- Marts: business-ready aggregates. The dashboard only reads from here.


-- ---------------------------------------------------------------------------
-- Daily KPI fact
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE marts.kpi_daily AS
WITH orders AS (
    SELECT
        order_date                                              AS date,
        count(*) FILTER (WHERE is_completed)                    AS orders,
        sum(net_revenue) FILTER (WHERE is_completed)            AS net_revenue,
        sum(gross_revenue) FILTER (WHERE is_completed)          AS gross_revenue,
        sum(discount_amount) FILTER (WHERE is_completed)        AS discount_amount,
        sum(units) FILTER (WHERE is_completed)                  AS units,
        sum(gross_margin) FILTER (WHERE is_completed)           AS gross_margin,
        sum(refund_amount)                                      AS refunds,
        count(DISTINCT customer_id) FILTER (WHERE is_completed) AS active_customers
    FROM core.fct_orders
    GROUP BY 1
),
new_customers AS (
    SELECT signup_date AS date, count(*) AS new_customers
    FROM core.dim_customers
    GROUP BY 1
),
sessions AS (
    SELECT
        session_date                                            AS date,
        count(*)                                                AS sessions,
        count(*) FILTER (WHERE step_rank >= 2)                  AS product_views,
        count(*) FILTER (WHERE step_rank >= 3)                  AS add_to_carts,
        count(*) FILTER (WHERE step_rank >= 4)                  AS checkouts,
        count(*) FILTER (WHERE is_purchase)                     AS purchases
    FROM core.fct_sessions
    GROUP BY 1
),
spend AS (
    SELECT date, sum(spend_usd) AS marketing_spend
    FROM core.fct_marketing_spend
    GROUP BY 1
)
SELECT
    d.date,
    d.date_key,
    d.month_start,
    d.year,
    d.month,
    d.day_of_week,
    d.day_name,
    d.is_weekend,
    coalesce(o.orders, 0)                                       AS orders,
    round(coalesce(o.net_revenue, 0), 2)                        AS net_revenue,
    round(coalesce(o.gross_revenue, 0), 2)                      AS gross_revenue,
    round(coalesce(o.discount_amount, 0), 2)                    AS discount_amount,
    coalesce(o.units, 0)                                        AS units,
    round(coalesce(o.gross_margin, 0), 2)                       AS gross_margin,
    round(coalesce(o.refunds, 0), 2)                            AS refunds,
    coalesce(o.active_customers, 0)                             AS active_customers,
    coalesce(n.new_customers, 0)                                AS new_customers,
    round(coalesce(o.net_revenue, 0) / nullif(o.orders, 0), 2)  AS aov,
    round(coalesce(o.gross_margin, 0) / nullif(o.net_revenue, 0) * 100, 2) AS gross_margin_pct,
    coalesce(s.sessions, 0)                                     AS sessions,
    coalesce(s.product_views, 0)                                AS product_views,
    coalesce(s.add_to_carts, 0)                                 AS add_to_carts,
    coalesce(s.checkouts, 0)                                    AS checkouts,
    coalesce(s.purchases, 0)                                    AS purchases,
    round(100.0 * coalesce(s.purchases, 0) / nullif(s.sessions, 0), 3) AS conversion_rate_pct,
    round(coalesce(sp.marketing_spend, 0), 2)                   AS marketing_spend
FROM core.dim_date d
LEFT JOIN orders o        ON o.date = d.date
LEFT JOIN new_customers n ON n.date = d.date
LEFT JOIN sessions s      ON s.date = d.date
LEFT JOIN spend sp        ON sp.date = d.date;

-- ---------------------------------------------------------------------------
-- Monthly KPI with growth, seasonality, rolling average and ROAS
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE marts.kpi_monthly AS
WITH monthly AS (
    SELECT
        month_start,
        sum(net_revenue)            AS net_revenue,
        sum(gross_revenue)          AS gross_revenue,
        sum(gross_margin)           AS gross_margin,
        sum(orders)                 AS orders,
        sum(units)                  AS units,
        sum(new_customers)          AS new_customers,
        sum(sessions)               AS sessions,
        sum(purchases)              AS purchases,
        sum(marketing_spend)        AS marketing_spend,
        sum(refunds)                AS refunds
    FROM marts.kpi_daily
    GROUP BY 1
)
SELECT
    month_start,
    year(month_start)                                               AS year,
    month(month_start)                                              AS month,
    strftime(month_start, '%b %Y')                                  AS month_label,
    round(net_revenue, 2)                                           AS net_revenue,
    round(gross_revenue, 2)                                         AS gross_revenue,
    round(gross_margin, 2)                                          AS gross_margin,
    orders,
    units,
    new_customers,
    sessions,
    round(marketing_spend, 2)                                       AS marketing_spend,
    round(refunds, 2)                                               AS refunds,
    round(net_revenue / nullif(orders, 0), 2)                       AS aov,
    round(gross_margin / nullif(net_revenue, 0) * 100, 2)           AS gross_margin_pct,
    round(100.0 * purchases / nullif(sessions, 0), 3)               AS conversion_rate_pct,
    round(net_revenue / nullif(marketing_spend, 0), 2)              AS roas,
    round(lag(net_revenue) OVER (ORDER BY month_start), 2)          AS prev_month_revenue,
    round(100.0 * (net_revenue - lag(net_revenue) OVER (ORDER BY month_start))
          / nullif(lag(net_revenue) OVER (ORDER BY month_start), 0), 2) AS mom_growth_pct,
    round(lag(net_revenue, 12) OVER (ORDER BY month_start), 2)      AS same_month_last_year,
    round(100.0 * (net_revenue - lag(net_revenue, 12) OVER (ORDER BY month_start))
          / nullif(lag(net_revenue, 12) OVER (ORDER BY month_start), 0), 2) AS yoy_growth_pct,
    round(avg(net_revenue) OVER (ORDER BY month_start ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 2) AS rolling_3m_avg,
    round(100.0 * net_revenue / sum(net_revenue) OVER (), 2)         AS revenue_share_pct
FROM monthly
ORDER BY month_start;

-- ---------------------------------------------------------------------------
-- Acquisition cohorts: monthly retention and revenue
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE marts.cohort_retention AS
WITH first_order AS (
    SELECT customer_id, min(order_date) AS first_order_date
    FROM core.fct_orders
    WHERE is_completed
    GROUP BY 1
),
cohorts AS (
    SELECT customer_id, date_trunc('month', first_order_date) AS cohort_month, first_order_date
    FROM first_order
),
activity AS (
    SELECT
        c.cohort_month,
        date_diff('month', c.cohort_month, date_trunc('month', o.order_date)) AS month_index,
        count(DISTINCT o.customer_id)                                        AS active_customers,
        count(*)                                                             AS orders,
        sum(o.net_revenue)                                                   AS revenue
    FROM cohorts c
    JOIN core.fct_orders o ON o.customer_id = c.customer_id AND o.is_completed
    GROUP BY 1, 2
),
sizes AS (
    SELECT cohort_month, count(*) AS cohort_size
    FROM cohorts
    GROUP BY 1
)
SELECT
    a.cohort_month,
    s.cohort_size,
    a.month_index,
    a.active_customers,
    a.orders,
    round(100.0 * a.active_customers / s.cohort_size, 2)                    AS retention_pct,
    round(a.revenue, 2)                                                     AS revenue,
    round(a.revenue / nullif(a.active_customers, 0), 2)                     AS revenue_per_active_customer
FROM activity a
JOIN sizes s USING (cohort_month)
ORDER BY 1, 3;

-- ---------------------------------------------------------------------------
-- RFM segmentation with a 12-month CLV estimate
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE marts.rfm AS
WITH as_of AS (
    SELECT max(order_date) AS max_date FROM core.fct_orders
),
base AS (
    SELECT
        customer_id,
        date_diff('day', max(order_date), (SELECT max_date FROM as_of))         AS recency_days,
        count(*)                                                               AS frequency,
        sum(net_revenue)                                                       AS monetary,
        sum(gross_margin)                                                      AS gross_margin,
        min(order_date)                                                        AS first_order_date,
        max(order_date)                                                        AS last_order_date,
        date_diff('day', min(order_date), (SELECT max_date FROM as_of))        AS tenure_days
    FROM core.fct_orders
    WHERE is_completed
    GROUP BY 1
),
scored AS (
    SELECT
        *,
        ntile(5) OVER (ORDER BY recency_days DESC)                              AS r_score,
        ntile(5) OVER (ORDER BY frequency ASC, monetary ASC)                    AS f_score,
        ntile(5) OVER (ORDER BY monetary ASC)                                   AS m_score
    FROM base
),
metrics AS (
    SELECT
        *,
        monetary / nullif(frequency, 0)                                         AS aov,
        gross_margin / nullif(monetary, 0)                                      AS margin_rate,
        frequency / greatest(tenure_days / 365.0, 0.08)                         AS orders_per_year
    FROM scored
)
SELECT
    customer_id,
    recency_days,
    frequency,
    round(monetary, 2)                                                          AS monetary,
    round(aov, 2)                                                               AS aov,
    r_score,
    f_score,
    m_score,
    round(0.5 * r_score + 0.3 * f_score + 0.2 * m_score, 1)                     AS rfm_score,
    CASE
        WHEN r_score >= 4 AND f_score >= 4 AND m_score >= 4 THEN 'Champions'
        WHEN r_score >= 3 AND f_score >= 3                  THEN 'Loyal'
        WHEN r_score >= 4 AND f_score <= 2                  THEN 'New / Promising'
        WHEN r_score <= 2 AND m_score >= 4                  THEN 'At Risk - High Value'
        WHEN r_score <= 2 AND f_score >= 3                  THEN 'At Risk'
        WHEN r_score <= 2                                   THEN 'Hibernating'
        WHEN r_score = 3                                    THEN 'Needs Attention'
        ELSE 'Occasional'
    END                                                                         AS segment,
    round(aov * orders_per_year * pow(0.5, recency_days / 180.0) * coalesce(margin_rate, 0.4), 2) AS clv_12m,
    first_order_date,
    last_order_date,
    round(tenure_days / 30.44, 1)                                               AS tenure_months
FROM metrics;

CREATE OR REPLACE TABLE marts.rfm_segments AS
SELECT
    segment,
    count(*)                                                        AS customers,
    round(100.0 * count(*) / sum(count(*)) OVER (), 2)               AS customer_share_pct,
    round(sum(monetary), 2)                                         AS revenue,
    round(100.0 * sum(monetary) / sum(sum(monetary)) OVER (), 2)     AS revenue_share_pct,
    round(avg(recency_days), 1)                                     AS avg_recency_days,
    round(avg(frequency), 2)                                        AS avg_frequency,
    round(avg(monetary), 2)                                         AS avg_monetary,
    round(avg(clv_12m), 2)                                          AS avg_clv_12m,
    round(sum(clv_12m), 2)                                          AS total_clv_12m
FROM marts.rfm
GROUP BY 1
ORDER BY revenue DESC;

-- ---------------------------------------------------------------------------
-- Funnel by channel and month
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE marts.funnel AS
SELECT
    month_start,
    channel,
    count(*)                                                    AS sessions,
    count(*) FILTER (WHERE step_rank >= 2)                      AS product_views,
    count(*) FILTER (WHERE step_rank >= 3)                      AS add_to_carts,
    count(*) FILTER (WHERE step_rank >= 4)                      AS checkouts,
    count(*) FILTER (WHERE step_rank = 5)                       AS purchases,
    round(100.0 * count(*) FILTER (WHERE step_rank >= 2) / count(*), 2)   AS view_rate_pct,
    round(100.0 * count(*) FILTER (WHERE step_rank >= 3) / nullif(count(*) FILTER (WHERE step_rank >= 2), 0), 2) AS cart_rate_pct,
    round(100.0 * count(*) FILTER (WHERE step_rank >= 4) / nullif(count(*) FILTER (WHERE step_rank >= 3), 0), 2) AS checkout_rate_pct,
    round(100.0 * count(*) FILTER (WHERE step_rank = 5) / nullif(count(*) FILTER (WHERE step_rank >= 4), 0), 2)  AS purchase_rate_pct,
    round(100.0 * count(*) FILTER (WHERE step_rank = 5) / count(*), 2)    AS conversion_rate_pct,
    round(avg(duration_seconds), 1)                             AS avg_duration_seconds,
    round(avg(duration_seconds) FILTER (WHERE step_rank = 5), 1) AS avg_purchase_duration_seconds
FROM core.fct_sessions
GROUP BY 1, 2
ORDER BY 1, 2;

CREATE OR REPLACE TABLE marts.funnel_summary AS
SELECT
    channel,
    count(*)                                                    AS sessions,
    count(*) FILTER (WHERE step_rank >= 2)                      AS product_views,
    count(*) FILTER (WHERE step_rank >= 3)                      AS add_to_carts,
    count(*) FILTER (WHERE step_rank >= 4)                      AS checkouts,
    count(*) FILTER (WHERE step_rank = 5)                       AS purchases,
    round(100.0 * count(*) FILTER (WHERE step_rank = 5) / count(*), 2) AS conversion_rate_pct,
    round(avg(duration_seconds) FILTER (WHERE step_rank = 5), 0) AS avg_purchase_duration_seconds,
    count(*) FILTER (WHERE is_bounce)                           AS bounces,
    round(100.0 * count(*) FILTER (WHERE is_bounce) / count(*), 2) AS bounce_rate_pct
FROM core.fct_sessions
GROUP BY 1
ORDER BY sessions DESC;

-- ---------------------------------------------------------------------------
-- Product performance with ABC (Pareto) classification
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE marts.product_performance AS
WITH revenue AS (
    SELECT
        product_id,
        sum(net_revenue) FILTER (WHERE is_completed)                AS revenue,
        sum(quantity) FILTER (WHERE is_completed)                   AS units,
        count(DISTINCT order_id) FILTER (WHERE is_completed)        AS orders,
        count(DISTINCT customer_id) FILTER (WHERE is_completed)     AS customers,
        sum(gross_margin) FILTER (WHERE is_completed)               AS gross_margin,
        avg(margin_pct) FILTER (WHERE is_completed)                 AS margin_pct,
        sum(discount_amount) FILTER (WHERE is_completed)            AS discount_amount,
        sum(gross_revenue) FILTER (WHERE is_completed)              AS gross_revenue
    FROM core.fct_order_items
    GROUP BY 1
),
returns AS (
    SELECT
        product_id,
        count(DISTINCT order_id) FILTER (WHERE is_returned)         AS returned_orders,
        count(DISTINCT order_id)                                    AS all_orders
    FROM core.fct_order_items
    GROUP BY 1
),
joined AS (
    SELECT
        p.product_id,
        p.product_name,
        p.category,
        p.list_price,
        p.unit_cost,
        coalesce(r.revenue, 0)                                      AS revenue,
        coalesce(r.units, 0)                                        AS units,
        coalesce(r.orders, 0)                                       AS orders,
        coalesce(r.customers, 0)                                    AS customers,
        coalesce(r.gross_margin, 0)                                 AS gross_margin,
        coalesce(r.margin_pct, 0)                                   AS margin_pct,
        round(coalesce(r.discount_amount, 0), 2)                    AS discount_amount,
        round(100.0 * coalesce(r.discount_amount, 0) / nullif(r.gross_revenue, 0), 2) AS avg_discount_pct,
        coalesce(t.returned_orders, 0)                              AS returned_orders,
        round(100.0 * coalesce(t.returned_orders, 0) / nullif(t.all_orders, 0), 2) AS return_rate_pct,
        p.is_active
    FROM core.dim_products p
    LEFT JOIN revenue r ON r.product_id = p.product_id
    LEFT JOIN returns t ON t.product_id = p.product_id
),
ranked AS (
    SELECT
        *,
        round(100.0 * revenue / nullif(sum(revenue) OVER (), 0), 3) AS revenue_share_pct,
        round(100.0 * sum(revenue) OVER (ORDER BY revenue DESC ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
              / nullif(sum(revenue) OVER (), 0), 3)                 AS cumulative_revenue_pct,
        row_number() OVER (ORDER BY revenue DESC)                   AS revenue_rank
    FROM joined
)
SELECT
    *,
    CASE WHEN cumulative_revenue_pct <= 70 THEN 'A'
         WHEN cumulative_revenue_pct <= 90 THEN 'B'
         ELSE 'C' END                                              AS abc_class
FROM ranked
ORDER BY revenue_rank;

-- ---------------------------------------------------------------------------
-- Channel economics: CAC, ROAS, LTV (first-touch attribution)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE marts.channel_economics AS
WITH spend AS (
    SELECT month_start, channel, sum(spend_usd) AS spend, sum(impressions) AS impressions, sum(clicks) AS clicks
    FROM core.fct_marketing_spend
    GROUP BY 1, 2
),
acquired AS (
    SELECT date_trunc('month', signup_date) AS month_start, acquisition_channel AS channel, count(*) AS new_customers
    FROM core.dim_customers
    GROUP BY 1, 2
),
attributed AS (
    SELECT
        date_trunc('month', o.order_date) AS month_start,
        c.acquisition_channel             AS channel,
        sum(o.net_revenue)                AS revenue,
        sum(o.gross_margin)               AS gross_margin,
        count(DISTINCT o.customer_id)     AS buyers,
        count(*)                          AS orders
    FROM core.fct_orders o
    JOIN core.dim_customers c USING (customer_id)
    WHERE o.is_completed
    GROUP BY 1, 2
),
clv AS (
    SELECT c.acquisition_channel AS channel, avg(r.clv_12m) AS avg_clv_12m, sum(r.clv_12m) AS total_clv_12m
    FROM marts.rfm r
    JOIN core.dim_customers c USING (customer_id)
    GROUP BY 1
)
SELECT
    s.month_start,
    s.channel,
    round(s.spend, 2)                                                AS spend,
    s.impressions,
    s.clicks,
    round(100.0 * s.clicks / nullif(s.impressions, 0), 3)            AS ctr_pct,
    round(s.spend / nullif(s.clicks, 0), 3)                          AS cpc,
    round(s.spend / nullif(s.impressions, 0) * 1000, 2)              AS cpm,
    coalesce(a.new_customers, 0)                                     AS new_customers,
    round(s.spend / nullif(a.new_customers, 0), 2)                   AS cac,
    round(coalesce(t.revenue, 0), 2)                                 AS attributed_revenue,
    round(coalesce(t.gross_margin, 0), 2)                            AS attributed_gross_margin,
    coalesce(t.orders, 0)                                            AS orders,
    round(coalesce(t.revenue, 0) / nullif(s.spend, 0), 2)            AS roas,
    round(coalesce(t.revenue, 0) / nullif(a.new_customers, 0), 2)    AS revenue_per_new_customer,
    round(coalesce(cl.avg_clv_12m, 0), 2)                            AS avg_clv_12m,
    round(coalesce(cl.avg_clv_12m, 0) / nullif(s.spend / nullif(a.new_customers, 0), 0), 2) AS ltv_cac_ratio
FROM spend s
LEFT JOIN acquired a      ON a.month_start = s.month_start AND a.channel = s.channel
LEFT JOIN attributed t    ON t.month_start = s.month_start AND t.channel = s.channel
LEFT JOIN clv cl          ON cl.channel = s.channel
ORDER BY 1, 2;

CREATE OR REPLACE TABLE marts.channel_summary AS
WITH spend AS (
    SELECT
        channel,
        sum(spend_usd)  AS spend,
        sum(impressions) AS impressions,
        sum(clicks)      AS clicks
    FROM core.fct_marketing_spend
    GROUP BY 1
),
acquired AS (
    SELECT acquisition_channel AS channel, count(*) AS new_customers
    FROM core.dim_customers
    GROUP BY 1
),
attributed AS (
    SELECT c.acquisition_channel AS channel, sum(o.net_revenue) AS revenue, sum(o.gross_margin) AS gross_margin
    FROM core.fct_orders o JOIN core.dim_customers c USING (customer_id)
    WHERE o.is_completed
    GROUP BY 1
),
clv AS (
    SELECT c.acquisition_channel AS channel, avg(r.clv_12m) AS avg_clv_12m
    FROM marts.rfm r JOIN core.dim_customers c USING (customer_id)
    GROUP BY 1
)
SELECT
    s.channel,
    round(s.spend, 2)                                       AS spend,
    s.impressions,
    s.clicks,
    coalesce(a.new_customers, 0)                            AS new_customers,
    round(s.spend / nullif(a.new_customers, 0), 2)          AS cac,
    round(coalesce(t.revenue, 0), 2)                        AS attributed_revenue,
    round(coalesce(t.gross_margin, 0), 2)                   AS attributed_gross_margin,
    round(coalesce(t.revenue, 0) / nullif(s.spend, 0), 2)   AS roas,
    round(coalesce(cl.avg_clv_12m, 0), 2)                   AS avg_clv_12m,
    round(coalesce(cl.avg_clv_12m, 0) / nullif(s.spend / nullif(a.new_customers, 0), 0), 2) AS ltv_cac_ratio
FROM spend s
LEFT JOIN acquired a   ON a.channel = s.channel
LEFT JOIN attributed t ON t.channel = s.channel
LEFT JOIN clv cl       ON cl.channel = s.channel
ORDER BY attributed_revenue DESC;

-- ---------------------------------------------------------------------------
-- Segment / country / device rollups used by the dashboard
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE marts.revenue_by_dimension AS
SELECT
    date_trunc('month', o.order_date) AS month_start,
    c.acquisition_channel,
    c.country,
    c.age_group,
    o.device,
    round(sum(o.net_revenue), 2) AS revenue,
    count(*)                     AS orders
FROM core.fct_orders o
JOIN core.dim_customers c USING (customer_id)
WHERE o.is_completed
GROUP BY 1, 2, 3, 4, 5;
