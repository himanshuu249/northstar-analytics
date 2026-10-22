-- Core: star schema. Facts are keyed to dimensions and are the only tables
-- the marts and dashboard are allowed to read.


CREATE OR REPLACE TABLE core.dim_date AS
WITH bounds AS (
    SELECT
        least(
            (SELECT min(order_date) FROM staging.orders),
            (SELECT min(session_date) FROM staging.sessions)
        ) AS lo,
        greatest(
            (SELECT max(order_date) FROM staging.orders),
            (SELECT max(session_date) FROM staging.sessions)
        ) AS hi
)
SELECT
    CAST(d AS DATE)                                    AS date,
    CAST(strftime(d, '%Y%m%d') AS INTEGER)             AS date_key,
    year(d)                                            AS year,
    quarter(d)                                         AS quarter,
    month(d)                                           AS month,
    strftime(d, '%b')                                  AS month_name,
    date_trunc('month', d)                             AS month_start,
    week(d)                                            AS week_of_year,
    dayofweek(d)                                       AS day_of_week,
    strftime(d, '%a')                                  AS day_name,
    dayofweek(d) IN (0, 6)                             AS is_weekend,
    ((month(d) = 11 AND day(d) BETWEEN 24 AND 30) OR (month(d) = 12 AND day(d) <= 2)) AS is_holiday_season
FROM bounds, generate_series(lo, hi, INTERVAL 1 DAY) AS t(d);

CREATE OR REPLACE TABLE core.fct_order_items AS
SELECT
    row_number() OVER (ORDER BY o.order_date, o.order_id, i.product_id) AS order_item_key,
    i.order_id,
    o.customer_id,
    i.product_id,
    o.order_date,
    d.date_key,
    d.month_start,
    o.channel,
    o.device,
    o.country,
    o.status,
    o.status = 'returned'                                                AS is_returned,
    o.status = 'cancelled'                                               AS is_cancelled,
    o.status = 'completed'                                               AS is_completed,
    i.quantity,
    i.unit_price,
    i.discount_amount,
    i.gross_revenue,
    i.net_revenue,
    round(i.unit_price - p.unit_cost, 2)                                 AS unit_margin,
    round(i.net_revenue - p.unit_cost * i.quantity, 2)                   AS gross_margin,
    round(CASE WHEN i.net_revenue > 0 THEN (i.net_revenue - p.unit_cost * i.quantity) / i.net_revenue ELSE 0 END, 4) AS margin_pct,
    o.session_id
FROM staging.order_items i
JOIN staging.orders o  ON o.order_id = i.order_id
JOIN staging.products p ON p.product_id = i.product_id
JOIN core.dim_date d   ON d.date = o.order_date;

CREATE OR REPLACE TABLE core.fct_orders AS
WITH items AS (
    SELECT
        order_id,
        count(*)                        AS items_count,
        sum(quantity)                   AS units,
        sum(gross_revenue)              AS gross_revenue,
        sum(discount_amount)            AS discount_amount,
        sum(net_revenue)                AS net_revenue,
        sum(gross_margin)               AS item_margin
    FROM core.fct_order_items
    GROUP BY order_id
)
SELECT
    o.order_id,
    o.customer_id,
    o.order_date,
    d.date_key,
    d.month_start,
    o.order_ts,
    o.channel,
    o.device,
    o.payment_method,
    o.country,
    o.status,
    o.status = 'completed'              AS is_completed,
    o.status = 'returned'               AS is_returned,
    i.items_count,
    i.units,
    i.gross_revenue,
    i.discount_amount,
    i.net_revenue,
    i.item_margin,
    o.shipping_amount,
    round(i.item_margin + o.shipping_amount, 2) AS gross_margin,
    CASE WHEN o.status = 'returned' THEN round(i.net_revenue + o.shipping_amount, 2) ELSE 0 END AS refund_amount,
    o.session_id
FROM staging.orders o
JOIN items i        ON i.order_id = o.order_id
JOIN core.dim_date d ON d.date = o.order_date;

CREATE OR REPLACE TABLE core.dim_customers AS
WITH order_stats AS (
    SELECT
        customer_id,
        min(order_date)                                                  AS first_order_date,
        max(order_date)                                                  AS last_order_date,
        count(*)                                                         AS lifetime_orders,
        sum(net_revenue)                                                 AS lifetime_revenue
    FROM core.fct_orders
    WHERE status = 'completed'
    GROUP BY customer_id
),
as_of AS (SELECT max(order_date) AS d FROM core.fct_orders)
SELECT
    c.customer_id,
    c.email,
    c.signup_date,
    date_trunc('month', c.signup_date)                                   AS signup_month,
    c.acquisition_channel,
    c.country,
    c.age_group,
    c.first_device,
    c.marketing_opt_in,
    o.first_order_date,
    o.last_order_date,
    coalesce(o.lifetime_orders, 0)                                       AS lifetime_orders,
    coalesce(o.lifetime_revenue, 0)                                      AS lifetime_revenue,
    coalesce(o.last_order_date >= (SELECT d FROM as_of) - INTERVAL 90 DAY, FALSE) AS is_active_90d
FROM staging.customers c
LEFT JOIN order_stats o USING (customer_id);

CREATE OR REPLACE TABLE core.dim_products AS
SELECT
    p.product_id,
    p.product_name,
    p.category,
    p.list_price,
    p.unit_cost,
    p.launch_date,
    p.discontinued_date,
    round(CASE WHEN p.list_price > 0 THEN (p.list_price - p.unit_cost) / p.list_price ELSE 0 END, 4) AS list_margin_pct,
    (p.discontinued_date IS NULL OR p.discontinued_date > (SELECT max(date) FROM core.dim_date)) AS is_active
FROM staging.products p;

CREATE OR REPLACE TABLE core.fct_sessions AS
SELECT
    row_number() OVER (ORDER BY s.session_ts, s.session_id) AS session_key,
    s.session_id,
    s.session_ts,
    s.session_date,
    d.date_key,
    d.month_start,
    s.channel,
    s.device,
    s.country,
    s.step_reached,
    CASE s.step_reached
        WHEN 'landing'      THEN 1
        WHEN 'product_view' THEN 2
        WHEN 'add_to_cart'  THEN 3
        WHEN 'checkout'     THEN 4
        WHEN 'purchase'     THEN 5
    END AS step_rank,
    s.duration_seconds,
    s.step_reached = 'purchase' AS is_purchase,
    s.step_reached = 'landing'  AS is_bounce
FROM staging.sessions s
JOIN core.dim_date d ON d.date = s.session_date;

CREATE OR REPLACE TABLE core.fct_marketing_spend AS
SELECT
    d.date_key,
    s.date,
    d.month_start,
    s.channel,
    s.spend_usd,
    s.impressions,
    s.clicks,
    s.new_customers_attributed
FROM staging.marketing_spend s
JOIN core.dim_date d ON d.date = s.date;
