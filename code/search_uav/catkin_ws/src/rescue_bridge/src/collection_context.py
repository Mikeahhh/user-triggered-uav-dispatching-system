def build_collection_context(record, active_id, instance_id):
    record = record or {}
    index = record.get('waypoint_index')
    total = record.get('source_waypoint_total')
    phase = record.get('phase', 'IDLE')
    arrived = record.get('search_area_arrival_observed') is True
    source_leg = (type(index) is int and type(total) is int and 0 <= index < total)
    ready = bool(active_id and record.get('execution_id') == active_id and arrived and source_leg
                 and record.get('collection_controller_valid') is True
                 and phase in {'HOVERING', 'NAVIGATING', 'WAITING_TARGET_ACCEPTANCE'})
    return {
        'schema_version': 2, 'context_epoch': instance_id, 'collection_ready': ready,
        'mission_id': record.get('mission_id', ''), 'execution_id': active_id or '',
        'mission_type': record.get('mission_type', ''), 'phase': phase,
        'waypoint_index': index, 'source_waypoint_total': total,
        'arrival_observed': arrived,
    }
