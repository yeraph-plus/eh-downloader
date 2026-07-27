FROM alpine:3.22 AS downloader

ARG HATH_VERSION=1.6.5
ARG HATH_SHA256=a42224c24008dbf895e2d178b1fa25589715788214f7ed526d1957217aaa912e

RUN apk add --no-cache curl unzip \
    && curl --fail --location --retry 3 \
      "https://repo.e-hentai.org/hath/HentaiAtHome_${HATH_VERSION}.zip" \
      --output /tmp/hath.zip \
    && echo "${HATH_SHA256}  /tmp/hath.zip" | sha256sum -c - \
    && mkdir -p /opt/hath \
    && unzip /tmp/hath.zip -d /opt/hath \
    && test -f /opt/hath/HentaiAtHome.jar

FROM eclipse-temurin:21-jre-jammy AS runtime

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin hath \
    && mkdir -p /hath/data /hath/cache /hath/log /hath/downloads /tmp/hath \
    && chown -R hath:hath /hath /tmp/hath

COPY --from=downloader --chown=hath:hath /opt/hath/ /opt/hath/
COPY --chown=hath:hath docker/hath-entrypoint.sh /usr/local/bin/hath-entrypoint
RUN chmod 0555 /usr/local/bin/hath-entrypoint

USER hath
WORKDIR /opt/hath

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD kill -0 1 || exit 1

ENTRYPOINT ["/usr/local/bin/hath-entrypoint"]
