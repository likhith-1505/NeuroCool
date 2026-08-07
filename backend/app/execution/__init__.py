"""The execution engine: turns an executed Decision into real per-rack
physics inputs, closing the control loop (telemetry -> decision ->
execution -> simulation changes -> telemetry updates -> frontend updates).

app.execution.manager.ExecutionManager is pure logic (no I/O), mirroring
app.simulation.scenario_manager.ScenarioManager exactly: it only tracks
active executions and computes the RackDrivers they contribute each tick.
app.execution.service.ExecutionService owns persistence and the
started/completed/failed lifecycle, mirroring app.ai.service.DecisionService.
"""
