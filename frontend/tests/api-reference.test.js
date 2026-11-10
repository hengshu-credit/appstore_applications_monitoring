import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { Window } from "happy-dom";
import {
  prepareApiSpec,
  filterApiSpec,
  apiGroups,
  createApiDocs,
} from "../src/api-reference.js";

const schema = {
  openapi: "3.1.0",
  info: { title: "观星", version: "0.5.0" },
  paths: {
    "/api/apps/{id}": {
      parameters: [
        { name: "id", in: "path", required: true, schema: { type: "string" } },
      ],
      get: {
        summary: "Read App",
        responses: {
          200: {
            description: "应用",
            content: {
              "application/json": {
                schema: { $ref: "#/components/schemas/App" },
              },
            },
          },
        },
      },
      delete: {
        summary: "Remove App",
        responses: { 204: { description: "Removed" } },
      },
    },
    "/api/keywords": {
      get: { summary: "Keywords", responses: { 200: { description: "OK" } } },
    },
    "/health": {
      get: {
        tags: ["服务状态"],
        summary: "Health",
        responses: { 200: { description: "OK" } },
      },
    },
  },
  tags: [{ name: "服务状态", description: "服务检查" }],
  components: {
    schemas: {
      App: { type: "object", properties: { name: { type: "string" } } },
    },
  },
};
const html = await readFile(
  new URL("../api-docs.html", import.meta.url),
  "utf8",
);

test("API groups preserve original operations, explicit tags, shared parameters and response references", () => {
  const before = structuredClone(schema);
  const spec = prepareApiSpec(schema);
  assert.deepEqual(schema, before);
  assert.deepEqual(spec.paths["/api/apps/{id}"].get.tags, ["应用与分组"]);
  assert.equal(apiGroups(spec).total, 4);
  assert.equal(
    spec.tags.find((tag) => tag.name === "服务状态").description,
    "服务检查",
  );
  const filtered = filterApiSpec(spec, "DELETE");
  assert.deepEqual(Object.keys(filtered.paths), ["/api/apps/{id}"]);
  assert.equal(filtered.paths["/api/apps/{id}"].get, undefined);
  assert.deepEqual(
    filtered.paths["/api/apps/{id}"].parameters,
    schema.paths["/api/apps/{id}"].parameters,
  );
  assert.deepEqual(filtered.components, schema.components);
  assert.deepEqual(
    filtered.tags.map((tag) => tag.name),
    ["应用与分组"],
  );
  assert.equal(apiGroups(filterApiSpec(spec, "关键词")).total, 1);
  assert.equal(apiGroups(filterApiSpec(spec, "read app")).total, 1);
  assert.equal(apiGroups(filterApiSpec(spec, "not-found")).total, 0);
  assert.equal(filterApiSpec(spec, "  "), spec);
  assert.throws(
    () => prepareApiSpec({ openapi: "3.1.0", paths: [] }),
    /OpenAPI/,
  );
});

test("docs load the protected JSON from the same origin and search locally without executing APIs", async () => {
  const win = new Window({
    url: "http://localhost:5174/api/docs?url=https://example.test/spec",
  });
  try {
    win.document.body.innerHTML = html;
    const requests = [],
      renders = [];
    const docs = createApiDocs({
      doc: win.document,
      fetcher: async (...args) => {
        requests.push(args);
        return { ok: true, status: 200, json: async () => schema };
      },
      renderReference: (options) => renders.push(options),
    });
    await docs.load();
    assert.equal(requests[0][0], "/api/openapi.json");
    assert.equal(requests[0][1].credentials, "same-origin");
    assert.equal(requests[0][1].headers.Accept, "application/json");
    assert.equal(win.document.querySelector("#api-doc-status").hidden, true);
    assert.equal(renders[0].validatorUrl, null);
    assert.deepEqual(renders[0].supportedSubmitMethods, []);
    assert.equal(renders[0].queryConfigEnabled, false);
    const search = win.document.querySelector("#api-doc-search");
    search.value = "关键词";
    search.dispatchEvent(new win.Event("input"));
    assert.equal(requests.length, 1);
    assert.equal(apiGroups(renders.at(-1).spec).total, 1);
    assert.equal(win.document.querySelectorAll("#api-doc-groups a").length, 1);
    search.value = "nothing-matches";
    search.dispatchEvent(new win.Event("input"));
    assert.equal(win.document.querySelector("#api-doc-empty").hidden, false);
    assert.equal(win.document.querySelector("#api-reference").hidden, true);
  } finally {
    await win.happyDOM.close();
  }
});

test("expired login and forbidden access never render the schema, and reloading recovers", async () => {
  const win = new Window();
  try {
    win.document.body.innerHTML = html;
    let status = 401,
      rendered = 0;
    const docs = createApiDocs({
      doc: win.document,
      fetcher: async () => ({
        status,
        ok: status === 200,
        json: async () => schema,
      }),
      renderReference: () => rendered++,
    });
    await docs.load();
    assert.equal(rendered, 0);
    assert.equal(win.document.querySelector("#api-doc-login").hidden, false);
    assert.match(
      win.document.querySelector("#api-doc-status-message").textContent,
      /登录管理员/,
    );
    status = 403;
    await docs.load();
    assert.equal(rendered, 0);
    assert.match(
      win.document.querySelector("#api-doc-status-message").textContent,
      /没有.*权限/,
    );
    status = 200;
    await docs.load();
    assert.equal(rendered, 1);
    assert.equal(win.document.querySelector("#api-doc-status").hidden, true);
    assert.equal(win.document.querySelector("#api-doc-login").hidden, true);
  } finally {
    await win.happyDOM.close();
  }
});

test("failed or malformed responses remain retryable and schema labels are plain text", async () => {
  const win = new Window();
  try {
    win.document.body.innerHTML = html;
    let mode = "network",
      rendered = 0;
    const docs = createApiDocs({
      doc: win.document,
      fetcher: async () => {
        if (mode === "network") throw new Error("offline");
        return {
          ok: true,
          status: 200,
          json: async () => {
            if (mode === "invalid") return {};
            const copy = structuredClone(schema);
            copy.paths["/health"].get.tags = ["<img src=x onerror=alert(1)>"];
            return copy;
          },
        };
      },
      renderReference: () => rendered++,
    });
    await docs.load();
    assert.equal(win.document.querySelector("#api-doc-retry").hidden, false);
    mode = "invalid";
    await docs.load();
    assert.equal(rendered, 0);
    assert.match(
      win.document.querySelector("#api-doc-status-message").textContent,
      /OpenAPI/,
    );
    mode = "valid";
    await docs.load();
    assert.equal(rendered, 1);
    assert.equal(win.document.querySelector("#api-doc-groups img"), null);
    assert.match(
      win.document.querySelector("#api-doc-groups").textContent,
      /<img/,
    );
  } finally {
    await win.happyDOM.close();
  }
});
