# OSRM routing engine with the road-network graph baked in.
#
# Why bake the graph into the image rather than mount it on a volume:
# provisioning a multi-GB graph onto a Fly volume from outside is awkward and
# manual, and the previous setup assumed a `/data/region.osrm` that no tooling
# ever produced — so OSRM would start and then fail every query. Building the
# graph at image-build time makes `fly deploy -c fly.osrm.toml` fully
# reproducible: no volume seeding, no filename drift, the graph and the engine
# ship together.
#
# The cost is a slow, memory-hungry build. osrm-extract needs RAM roughly equal
# to the .pbf size, and a US state extract is ~0.5-1.5 GB. Build on a machine
# (or Fly remote builder) with enough memory, or point OSRM_PBF_URL at a smaller
# metro extract from https://extract.bbbike.org for a fast build that still
# covers a single city.
#
# The default region is Texas, which covers the bundled Dallas sample fleet.
# Override at build time:
#   fly deploy -c fly.osrm.toml \
#     --build-arg OSRM_PBF_URL=https://download.geofabrik.de/europe/monaco-latest.osm.pbf

# ---- Build the graph ----
FROM ghcr.io/project-osrm/osrm-backend:latest AS graph

ARG OSRM_PBF_URL=https://download.geofabrik.de/north-america/us/texas-latest.osm.pbf
WORKDIR /data

# extract -> partition -> customize is the MLD pipeline, and it MUST match the
# `--algorithm mld` the runtime uses below. The graph is renamed to region.osrm
# so the runtime command has a stable, region-independent path.
RUN curl -fL -o region.osm.pbf "${OSRM_PBF_URL}" \
    && osrm-extract -p /opt/car.lua region.osm.pbf \
    && osrm-partition region.osrm \
    && osrm-customize region.osrm \
    && rm region.osm.pbf

# ---- Runtime ----
FROM ghcr.io/project-osrm/osrm-backend:latest
COPY --from=graph /data /data

EXPOSE 5000
# --max-table-size must exceed the largest matrix RouteBench requests, which is
# the fleet benchmark at (total stops + depots)^2. 10000 covers 100 locations.
CMD ["osrm-routed", "--algorithm", "mld", "/data/region.osrm", "--max-table-size", "10000"]
