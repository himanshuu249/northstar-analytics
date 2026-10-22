-- Staging: type-cast, normalise and de-duplicate the raw exports.
-- Every rule here has a matching "issue count" in analytics/quality.py so the
-- dashboard can show exactly what the cleaning layer removed.


CREATE OR REPLACE TABLE staging.customers AS
WITH src AS (
    SELECT
        customer_id,
        lower(trim(email))                        AS email,
        try_cast(signup_date AS DATE)             AS signup_date,
        acquisition_channel,
        country,
        age_group,
        first_device,
        marketing_opt_in,
        row_number() OVER (PARTITION BY customer_id ORDER BY signup_date) AS rn
    FROM raw.customers
    WHERE customer_id IS NOT NULL
),
mapped AS (
    SELECT
        s.customer_id,
        s.email,
        s.signup_date,
        coalesce(a.canonical, 'unknown')          AS acquisition_channel,
        upper(trim(s.country))                    AS country,
        s.age_group,
        s.first_device,
        coalesce(s.marketing_opt_in, FALSE)       AS marketing_opt_in,
        (s.email LIKE '%@example.com' OR s.email LIKE 'test%') AS is_test_account,
        s.rn
    FROM src s
    LEFT JOIN staging.channel_alias a ON lower(trim(s.acquisition_channel)) = a.alias
)
SELECT
    customer_id, email, signup_date, acquisition_channel, country,
    age_group, first_device, marketing_opt_in
FROM mapped
WHERE rn = 1
  AND NOT is_test_account
  AND email IS NOT NULL
  AND signup_date IS NOT NULL;

CREATE OR REPLACE TABLE staging.products AS
WITH src AS (
    SELECT
        product_id,
        trim(product_name)                          AS product_name,
        nullif(trim(category), '')                  AS category,
        CAST(list_price AS DOUBLE)                  AS list_price,
        CAST(unit_cost AS DOUBLE)                   AS unit_cost,
        try_cast(launch_date AS DATE)               AS launch_date,
        try_cast(discontinued_date AS DATE)         AS discontinued_date,
        row_number() OVER (PARTITION BY product_id ORDER BY launch_date) AS rn
    FROM raw.products
)
SELECT
    product_id,
    product_name,
    coalesce(category, 'Uncategorized')             AS category,
    coalesce(list_price, 0)                         AS list_price,
    coalesce(unit_cost, round(coalesce(list_price, 0) * 0.55, 2)) AS unit_cost,
    coalesce(launch_date, DATE '1900-01-01')        AS launch_date,
    discontinued_date
FROM src
WHERE rn = 1;

CREATE OR REPLACE TABLE staging.orders AS
WITH src AS (
    SELECT
        order_id,
        customer_id,
        try_cast(order_ts AS TIMESTAMP)             AS order_ts,
        channel,
        device,
        payment_method,
        country,
        session_id,
        lower(trim(status))                         AS status,
        items_count,
        units,
        gross_amount,
        abs(coalesce(discount_amount, 0))           AS discount_amount,
        net_amount,
        cost_amount,
        shipping_amount,
        refund_amount,
        row_number() OVER (PARTITION BY order_id ORDER BY try_cast(order_ts AS TIMESTAMP)) AS rn
    FROM raw.orders
    WHERE order_id IS NOT NULL
)
SELECT
    s.order_id,
    s.customer_id,
    s.order_ts,
    CAST(s.order_ts AS DATE)                        AS order_date,
    coalesce(a.canonical, 'unknown')                AS channel,
    s.device,
    coalesce(s.payment_method, 'unknown')           AS payment_method,
    coalesce(upper(trim(s.country)), 'UNKNOWN')     AS country,
    s.session_id,
    CASE WHEN s.status IN ('completed', 'returned', 'cancelled')
         THEN s.status ELSE 'completed' END         AS status,
    CAST(s.shipping_amount AS DOUBLE)               AS shipping_amount
FROM src s
JOIN staging.customers c ON c.customer_id = s.customer_id     -- drops nulls + orphans
LEFT JOIN staging.channel_alias a ON lower(trim(s.channel)) = a.alias
WHERE s.rn = 1
  AND s.order_ts IS NOT NULL                                  -- drops null timestamps
  AND CAST(s.order_ts AS DATE) <= current_date                -- drops future-dated rows
  AND CAST(s.order_ts AS DATE) >= c.signup_date;              -- drops pre-signup rows

CREATE OR REPLACE TABLE staging.order_items AS
SELECT
    i.order_id,
    i.product_id,
    CAST(i.quantity AS INTEGER)                     AS quantity,
    CAST(i.unit_price AS DOUBLE)                    AS unit_price,
    greatest(abs(CAST(i.discount_amount AS DOUBLE)), 0) AS discount_amount,
    round(CAST(i.quantity AS DOUBLE) * CAST(i.unit_price AS DOUBLE), 2) AS gross_revenue,
    round(
        CAST(i.quantity AS DOUBLE) * CAST(i.unit_price AS DOUBLE)
        - abs(CAST(i.discount_amount AS DOUBLE)), 2
    )                                               AS net_revenue
FROM raw.order_items i
JOIN staging.orders o   ON o.order_id = i.order_id
JOIN staging.products p ON p.product_id = i.product_id
WHERE i.quantity > 0              -- drops negative-quantity refund lines
  AND i.unit_price > 0;

CREATE OR REPLACE TABLE staging.sessions AS
WITH src AS (
    SELECT
        session_id,
        try_cast(session_ts AS TIMESTAMP)            AS session_ts,
        lower(trim(channel))                         AS channel,
        device,
        upper(trim(country))                         AS country,
        lower(trim(step_reached))                    AS step_reached,
        CAST(duration_seconds AS INTEGER)            AS duration_seconds,
        row_number() OVER (PARTITION BY session_id ORDER BY session_ts) AS rn
    FROM raw.sessions
)
SELECT
    s.session_id,
    s.session_ts,
    CAST(s.session_ts AS DATE)                       AS session_date,
    coalesce(a.canonical, 'unknown')                 AS channel,
    s.device,
    coalesce(s.country, 'UNKNOWN')                   AS country,
    CASE WHEN s.step_reached IN ('landing', 'product_view', 'add_to_cart', 'checkout', 'purchase')
         THEN s.step_reached ELSE 'landing' END      AS step_reached,
    greatest(coalesce(s.duration_seconds, 0), 0)     AS duration_seconds
FROM src s
LEFT JOIN staging.channel_alias a ON s.channel = a.alias
WHERE s.rn = 1
  AND s.session_ts IS NOT NULL
  AND CAST(s.session_ts AS DATE) <= current_date;

CREATE OR REPLACE TABLE staging.marketing_spend AS
WITH src AS (
    SELECT
        CAST(date AS DATE)                           AS date,
        lower(trim(channel))                         AS channel,
        CAST(spend_usd AS DOUBLE)                    AS spend_usd,
        CAST(impressions AS BIGINT)                  AS impressions,
        CAST(clicks AS BIGINT)                       AS clicks,
        CAST(new_customers_attributed AS INTEGER)    AS new_customers_attributed,
        row_number() OVER (PARTITION BY date, channel) AS rn
    FROM raw.marketing_spend
)
SELECT
    s.date,
    coalesce(a.canonical, 'unknown')                 AS channel,
    greatest(s.spend_usd, 0)                         AS spend_usd,
    greatest(s.impressions, 0)                       AS impressions,
    greatest(s.clicks, 0)                            AS clicks,
    greatest(s.new_customers_attributed, 0)          AS new_customers_attributed
FROM src s
LEFT JOIN staging.channel_alias a ON s.channel = a.alias
WHERE s.rn = 1;
