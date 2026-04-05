"""
End-to-end Phase 1 demo.

Usage:
    python run_simulation.py                         # makespan objective, no failures
    python run_simulation.py --objective weighted    # weighted objective
    python run_simulation.py --failures              # inject a battery failure
    python run_simulation.py --save results/run.gif  # save animation
"""

import argparse
import numpy as np

from src.field.generator import synthetic_field, generate_strips
from src.optimizer.milp import assign_strips, DroneSpec
from src.simulation.engine import simulate
from src.viz.renderer import animate


def main():
    parser = argparse.ArgumentParser(description="Drone fleet failure-aware optimization demo")
    parser.add_argument("--objective", choices=["makespan", "weighted"], default="makespan",
                        help="Optimization objective (default: makespan)")
    parser.add_argument("--priority-weight", type=float, default=1.0,
                        help="Reward weight for priority coverage (weighted mode only)")
    parser.add_argument("--makespan-penalty", type=float, default=0.5,
                        help="Penalty per second of makespan (weighted mode only)")
    parser.add_argument("--rows", type=int, default=8, help="Grid rows")
    parser.add_argument("--cols", type=int, default=8, help="Grid columns")
    parser.add_argument("--n-drones", type=int, default=3, help="Number of drones")
    parser.add_argument("--failures", action="store_true",
                        help="Inject a battery failure on drone 0 at step 10")
    parser.add_argument("--save", metavar="PATH", default=None,
                        help="Save animation to this path (e.g. results/run.gif)")
    parser.add_argument("--interval", type=int, default=200,
                        help="Animation frame interval in ms")
    args = parser.parse_args()

    # 1. Field
    print(f"[1/4] Generating {args.rows}x{args.cols} synthetic field ...")
    field_grid = synthetic_field(nrows=args.rows, ncols=args.cols)
    strips = generate_strips(field_grid, seconds_per_cell=2.0)
    print(f"      {len(strips)} strips generated")

    # 2. Drones
    drones = [
        DroneSpec(id=i, battery=100.0, spray_capacity=100.0)
        for i in range(args.n_drones)
    ]

    # 3. Optimize
    print(f"[2/4] Optimizing ({args.objective} objective) ...")
    result = assign_strips(
        strips=strips,
        drones=drones,
        objective_mode=args.objective,
        priority_weight=args.priority_weight,
        makespan_penalty=args.makespan_penalty,
    )
    print(f"      Status: {result.status}  |  Makespan: {result.makespan}s  "
          f"|  Solve time: {result.solve_time}s")
    for d_id, strip_ids in result.assignment.items():
        print(f"      Drone {d_id}: strips {strip_ids}")

    # 4. Simulate
    failure_events = []
    if args.failures:
        failure_events = [{"timestep": 10, "drone_id": 0, "type": "battery"}]
        print("[3/4] Simulating with failure injection (drone 0 @ step 10) ...")
    else:
        print("[3/4] Simulating ...")

    state_history = simulate(
        strips=strips,
        drones=drones,
        result=result,
        nrows=args.rows,
        ncols=args.cols,
        failure_events=failure_events,
        replan_objective=args.objective,
        replan_priority_weight=args.priority_weight,
        replan_makespan_penalty=args.makespan_penalty,
    )
    print(f"      {len(state_history)} timesteps recorded")

    # 5. Visualise
    print("[4/4] Rendering animation ...")
    animate(
        state_history=state_history,
        nrows=args.rows,
        ncols=args.cols,
        interval_ms=args.interval,
        save_path=args.save,
        show=True,
    )


if __name__ == "__main__":
    main()
