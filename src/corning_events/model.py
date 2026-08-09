"""The canonical Event record.

Lands in M1. Mirrors the canonical data model in spec section 8: event_id,
source_id, source_uid, title, description, start, end, all_day, venue_name,
address, lat, lon, city_tag, county_tag, categories, cost, ticket_url,
source_url, original_url, recurrence_parent_id. Everything is nullable except
source_id, source_uid, title and start. Datetimes are stored as UTC.
"""
