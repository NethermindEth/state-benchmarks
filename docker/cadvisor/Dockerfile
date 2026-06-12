# cAdvisor patched for hosts where Docker runs the containerd image store
# (storage driver "overlayfs"). Upstream (≤ v0.52.x) hard-fails registering
# every container because it reads the legacy layerdb
# (/var/lib/docker/image/<driver>/layerdb), which that backend doesn't have —
# so all per-container series lose their `name` label and every
# name="benchmark_*" query (Grafana client variable, aggregator cpu/rss/net/
# disk queries) returns nothing. See https://github.com/google/cadvisor/issues/3344.
#
# The patch makes the rw-layer-ID lookup non-fatal (return "" instead of an
# error). The layer ID only feeds the rw-layer *usage* metric
# (container_fs_usage_bytes), which this repo doesn't use and which the baked-in
# --disable_metrics flag below turns off anyway (the list restates cAdvisor's
# defaults because the flag replaces rather than appends). CPU, memory, network
# and diskIO (container_fs_reads/writes_*) metrics are unaffected.
#
# Build and use (no daemon changes needed):
#   docker build -t cadvisor-layerdb-fix:v0.49.1 docker/cadvisor
#   echo 'CADVISOR_IMAGE=cadvisor-layerdb-fix:v0.49.1' >> .env
#   docker compose -f docker/docker-compose.monitoring.yml up -d --force-recreate cadvisor

FROM golang:1.22-alpine AS build
RUN apk add --no-cache git
ARG CADVISOR_VERSION=v0.49.1
RUN git clone --depth 1 --branch ${CADVISOR_VERSION} https://github.com/google/cadvisor.git /src
WORKDIR /src
# Tolerate a missing legacy layerdb instead of failing container registration.
# The grep asserts the patch landed — the build fails loudly if upstream moves
# the line.
RUN sed -i 's#return "", fmt.Errorf("failed to identify the read-write layer ID.*#return "", nil#' \
      container/docker/handler.go && \
    grep -q 'return "", nil' container/docker/handler.go
# The cadvisor binary lives in the cmd/ submodule (it `replace`s the library
# with ../, so the patch above is what gets compiled).
RUN cd cmd && CGO_ENABLED=0 GOFLAGS=-mod=mod go build -o /cadvisor .

FROM alpine:3.20
COPY --from=build /cadvisor /usr/bin/cadvisor
EXPOSE 8080
ENTRYPOINT ["/usr/bin/cadvisor", "-logtostderr", \
  "--disable_metrics=advtcp,cpu_topology,cpuset,hugetlb,memory_numa,process,referenced_memory,resctrl,sched,tcp,udp,disk"]
