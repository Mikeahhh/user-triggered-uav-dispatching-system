def arrive(manager):
    state = manager.snapshot()
    identity = (state['mission_id'], state['execution_id'], state['waypoint_index'])
    manager.feedback(*identity, 'ACCEPTED')
    return manager.feedback(*identity, 'ARRIVED', position_valid=True, feedback_seq=1)


def complete_hover(manager, clock):
    state = manager.snapshot()
    if state['phase'] != 'HOVERING':
        raise AssertionError('arrival must be observed before hover completion')
    identity = (state['mission_id'], state['execution_id'], state['waypoint_index'])
    finish = clock[0] + state['hover_seconds']
    sequence = manager.last_feedback_seq
    while clock[0] < finish:
        clock[0] = min(finish, clock[0] + 0.5)
        sequence += 1
        manager.feedback(*identity, 'HOLDING', position_valid=True, feedback_seq=sequence)
        result = manager.tick()
        if result is not None and result['phase'] != 'HOVERING':
            return result
    return manager.tick()


def complete_execution(manager, clock):
    while manager.snapshot()['phase'] in ('WAITING_TARGET_ACCEPTANCE', 'HOVERING'):
        if manager.snapshot()['phase'] == 'WAITING_TARGET_ACCEPTANCE':
            arrive(manager)
        complete_hover(manager, clock)
    if manager.snapshot()['phase'] != 'LAND_REQUEST_PENDING':
        raise AssertionError('all supplied targets and final hover must complete')
    return manager.land_result(True)
