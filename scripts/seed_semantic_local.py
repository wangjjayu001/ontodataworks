"""Run the explicit local metadata migration inside the configured API container."""
from runtime import compose


if __name__ == "__main__":
    print(compose("exec", "-T", "api", "python", "-m", "app.semantic_seed", "--workspace", "3"))
