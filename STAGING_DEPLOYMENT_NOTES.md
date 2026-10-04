# Staging deployment notes - Part P-104 (resource limits)

Status: limits BUILT and ENFORCEMENT PROVEN LOCALLY (2026-10-04). Genuine
deployment onto the shared VPS is BLOCKED (Section 7 item 6 VPS specifics and
item 9 domain are still OPEN). Every number below is a conservative PLACEHOLDER.

## 1. Why this exists

The architecture (Section 22) names an operational risk: this project's heavier
profile (WebSocket connections, Celery workers, media processing) could degrade
the sibling projects (Eduvia, Managora, Shark, 2ROOTS) that share the VPS.
P-104 turns that warning into a mitigation that Docker itself enforces: every
container of the staging stack has an explicit CPU, memory and swap limit.
"No limits at all" must never ship.

## 2. The limits (docker-compose.staging.yml)

| Container     | CPUs | Memory | Swap limit | Reasoning (provisional)                                                                 |
|---------------|------|--------|------------|-----------------------------------------------------------------------------------------|
| db            | 1.00 | 1G     | 1G         | Postgres is the heaviest stateful service; 1 CPU stops a heavy query taking the host.   |
| redis         | 0.25 | 256M   | 256M       | Single-threaded; one instance serves Celery broker, cache and Channels layer.           |
| web           | 0.50 | 512M   | 512M       | One daphne process for HTTP + WebSocket; memory grows with open WebSocket connections.  |
| celery_worker | 0.50 | 512M   | 512M       | Run with --concurrency=2 (see below); media processing (ffmpeg) is the heavy path.      |
| celery_beat   | 0.25 | 256M   | 256M       | A single light scheduler process.                                                       |

Worst case if every container hit its limit at once: 2.5 CPUs and 2560 MB.

Each limit carries this comment in the compose file:
"Conservative placeholder pending real sibling-project usage data (Section 7
item 6) - revisit once known."

How it is enforced:
- `deploy.resources.limits` (cpus, memory) is applied by Docker Compose v2.
- `memswap_limit` equals the memory limit, so a container cannot spill into
  swap and is killed at the limit instead of silently using more memory.
- `celery_worker` runs `--concurrency=2`. Without it Celery starts one process
  per host core, and on a big host that alone can exceed 512 MB.

## 3. Verification performed (local, not the VPS)

Environment: Docker Desktop, Docker 29.7.2, Compose 5.5.1, cgroup v2, 12 CPUs,
about 16 GB RAM. Isolated compose project `scd-p104`, web on port 8010. Script:
`D:\Cavallo\_scripts\p104_step2.ps1` (outside the repo). Result: PASS.

| Check                                              | Result                                                            |
|----------------------------------------------------|-------------------------------------------------------------------|
| All 5 services start, 0 restarts, db/redis healthy | PASS (worker "ready", beat started, web answers HTTP 404 on /)    |
| Limits on the RUNNING containers (docker inspect)  | PASS: Memory, MemorySwap and NanoCpus equal the table above       |
| CPU: 2 busy processes inside web (limit 0.5 CPU)   | PASS: 49.4% and 51.5% (unlimited would be about 200%), nr_throttled 64 -> 247 |
| OOM web (512M), allocate towards 1536 MB           | PASS: killed (exit 137), stopped at 416 MB, oom_kill 0 -> 1       |
| OOM celery_worker (512M)                           | PASS: killed (exit 137), stopped at 320 MB, oom_kill 0 -> 1       |
| OOM db (1G), `tail /dev/zero`                      | PASS: killed (exit 137), oom_kill 0 -> 1, db healthy afterwards   |
| OOM redis (256M), `tail /dev/zero`                 | PASS: killed (exit 137), oom_kill 0 -> 1                          |

Idle memory observed (useful for sizing): web 84 MiB, celery_worker 172 MiB,
celery_beat 101 MiB, db 30 MiB, redis 3.6 MiB. These are local numbers, NOT
VPS data and NOT a substitute for Section 7 item 6.

Note on how the proof works: the memory test runs inside a container, so the
kernel kills the test process, not the container's main process. The container
stays running with 0 restarts; the proof is the cgroup `oom_kill` counter
going up, exit code 137 from the inner shell, and the allocation stopping near
the limit instead of reaching the 1536 MB target.

## 4. How to re-run the proof

From `D:\Cavallo\scd-backend` (Docker Desktop running):

    & D:\Cavallo\_scripts\p104_step2.ps1

It uses its own project name (`scd-p104`) and removes only that project's
containers and volumes at the end. The same idea works on any Docker host:
start the stack, run `docker inspect` on each container (Memory, MemorySwap,
NanoCpus), then run a memory hog inside one container and confirm exit 137 and
a higher `oom_kill` in `/sys/fs/cgroup/memory.events`.

## 5. Not done / provisional (read before relying on these numbers)

1. VPS deployment is BLOCKED: Section 7 item 6 (VPS specifics, existing
   container inventory) and item 9 (domain) are OPEN. The numbers must be
   revisited once the real sibling usage is known.
2. Enforcement was proven on Docker Desktop (cgroup v2), not on the VPS. On the
   VPS check: Docker Compose v2 plugin (legacy docker-compose v1 ignores
   `deploy.resources.limits` unless run with --compatibility), and that the
   kernel supports swap limits (`docker info` prints a warning if not; then
   `memswap_limit` is not enforced). Repeat the proof there once it exists.
3. `docker-compose.prod.yml` still has NO limits. P-106 must apply the same
   limits there; production must never ship unlimited containers.
4. Redis has no `maxmemory`. At 256M the container is OOM-killed and (no
   persistence configured) its data is lost. Because one Redis serves the
   Celery broker, cache and Channels layer, choosing `maxmemory` and an
   eviction policy is an owner decision; not changed in this part.
5. Reel transcoding (ffmpeg, P-042) was NOT exercised under 0.5 CPU / 512M. A
   large video may be slow or be OOM-killed. Test with a realistic video and
   raise the worker limits if needed.
6. `--concurrency=2` is set only in the staging compose file. The dev and prod
   files are unchanged (P-106 should decide for prod).
7. P-105 (monitoring go-live) should watch for containers touching their limits
   (restarts, OOM kills, CPU throttling) so the sizing can be corrected.
