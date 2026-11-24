"""Literal, case-insensitive matching for application filters in a market."""

LISTING_NAME = "NULLIF(JSON_UNQUOTE(JSON_EXTRACT(l.payload,'$.name')),'null')"


def identity_filter(app_id, query):
    # Callers join the catalog as a and the row's market listing as l.
    columns = (f'CONVERT({app_id} USING utf8mb4)', 'a.name', LISTING_NAME)
    return ('(' + ' OR '.join(f'LOCATE(LOWER(%s),LOWER({column}))>0' for column in columns) + ')',
            [query.strip()] * len(columns))


def market_joins(row):
    return (f' LEFT JOIN apps a ON a.store={row}.store AND a.app_id={row}.app_id'
            f' LEFT JOIN listings l ON l.store={row}.store AND l.app_id={row}.app_id'
            f' AND l.country={row}.country AND l.language={row}.language ')
