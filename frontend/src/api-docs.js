import SwaggerUI from "swagger-ui-dist/swagger-ui-es-bundle.js";
import "swagger-ui-dist/swagger-ui.css";
import "./theme.css";
import "./fonts.css";
import "./api-docs.css";
import { initTheme } from "./theme.js";
import { createApiDocs } from "./api-reference.js";

initTheme();
const docs = createApiDocs({
  doc: document,
  fetcher: window.fetch.bind(window),
  renderReference: (options) => SwaggerUI(options),
});
await docs.load();
