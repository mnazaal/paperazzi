FROM node:lts-alpine

RUN apk add --no-cache git

WORKDIR /app

RUN git clone --depth=1 https://github.com/zotero/translation-server.git . && \
    git clone --depth=1 https://github.com/zotero/translators.git modules/translators/ && \
    git clone --depth=1 https://github.com/zotero/utilities.git modules/utilities/ && \
    git clone --depth=1 https://github.com/zotero/translate.git modules/translate/ && \
    git clone --depth=1 https://github.com/zotero/zotero-schema.git modules/zotero-schema/

RUN npm install

EXPOSE 1969
CMD ["npm", "start"]
