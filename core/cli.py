import argparse
from core.maestro import VaultManager, conduct


def main() -> None:
    parser = argparse.ArgumentParser(description="Maestro Protocol CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create a new project from a template")
    init.add_argument("project")
    init.add_argument("--template", default="python_app")

    run = sub.add_parser("run", help="Run the orchestrator")
    run.add_argument("project")
    run.add_argument("prompt")
    run.add_argument("--routing-model", default=None)
    run.add_argument("--agent-model", default=None)
    run.add_argument("--max-iterations", type=int, default=10)

    status = sub.add_parser("status", help="Show current project state")
    status.add_argument("project")

    manifest = sub.add_parser("manifest", help="Show compact workspace manifest for a project")
    manifest.add_argument("project")
    manifest.add_argument("--root", action="append", default=["04_WORKSPACE/"])

    args = parser.parse_args()
    vault = VaultManager()

    if args.command == "init":
        vault.init_project(args.project, args.template)
        print(f"Created project: {args.project}")
    elif args.command == "run":
        conduct(
            args.project,
            args.prompt,
            routing_model=args.routing_model,
            agent_model=args.agent_model,
            max_iterations=args.max_iterations,
        )
    elif args.command == "status":
        print(vault.load_current_state(args.project))
    elif args.command == "manifest":
        for entry in vault.build_file_manifest(args.project, args.root):
            print(f"{entry.path} | {entry.size_bytes} bytes | sha256:{entry.sha256_12}")


if __name__ == "__main__":
    main()
