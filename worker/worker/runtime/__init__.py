"""Runtime helpers — git clone, docker buildx, ECS client. Worker-only.

Kept out of `shared` so the API image doesn't pull docker CLI / AWS CLI deps.
Modules to add as we implement: git_clone, docker_build, ecs_client.
"""
