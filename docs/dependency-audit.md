# Optional Node tool advisories

The full image's locked Node developer-tool graph for Aicodebox v0.18.0 reports 78 advisories: 8 critical, 41 high, 26 moderate and 3 low. These findings concern installed third-party packages, not findings in Aicodebox source. This is not evidence that Aicodebox source is free of vulnerabilities.

## Exposure and retained dependencies

`Dockerfile.full` installs this graph under `/opt/aicodebox-node` and links its commands into `/usr/local/bin`. The minimal image does not install this optional graph. The Python API, MCP, Telegram and cron dispatchers do not import these Node modules. An agent, user or adapter can still invoke the installed commands, so the vulnerabilities remain reachable when those tools process hostile input, including projects, templates, archives, collections and network responses. A compromised tool has the container's access, including any mounted workspace, credentials and Docker socket.

The lockfile refresh updates transitive dependencies within the selected packages' declared constraints. The remaining findings below still occur under those pinned parent tools. Some upstream packages have no published fix; other fixes require changing or overriding the parent dependency constraints. This release retains the advertised tools instead of removing them or applying untested cross-major overrides. It does not suppress the advisories or claim the commands are safe for untrusted input. Prefer the minimal image when these tools are unnecessary.

Each dependency path below records the installed route from a top-level tool to the affected package. The linked advisory describes the affected operation and exploit conditions. The fixed range is the audit database's recommendation, not a claim that the parent tool supports that replacement.

## Audit reproduction

Run the pinned package manager from `full-node/`:

```bash
corepack pnpm@12.12.1 audit --prod --json
```

The image's frozen lockfile, package versions and disabled install scripts remain unchanged by this read-only command. Advisory databases change, so later scans can report different findings for the same lockfile.

## Outstanding findings

| Advisory | Package | Severity | Affected range | Fixed range | Installed dependency paths |
| --- | --- | --- | --- | --- | --- |
| [GHSA-pfrx-2q88-qq97](https://github.com/advisories/GHSA-pfrx-2q88-qq97) | got | moderate | `<11.8.5` | `>=11.8.5` | `.>@vue/cli>download-git-repo>download>got` |
| [GHSA-phwq-j96m-2c2q](https://github.com/advisories/GHSA-phwq-j96m-2c2q) | ejs | critical | `<3.1.7` | `>=3.1.7` | `.>express-generator>ejs` |
| [GHSA-3jfq-g458-7qm9](https://github.com/advisories/GHSA-3jfq-g458-7qm9) | tar | high | `<3.2.2` | `>=5.0.3` | `.>create-react-app>tar-pack>tar` |
| [GHSA-8jmw-wjr8-2x66](https://github.com/advisories/GHSA-8jmw-wjr8-2x66) | git-clone | high | `<=0.2.0` | `none reported` | `.>@vue/cli>download-git-repo>git-clone` |
| [GHSA-5955-9wpr-37jh](https://github.com/advisories/GHSA-5955-9wpr-37jh) | tar | high | `<4.4.18` | `>=5.0.3` | `.>create-react-app>tar-pack>tar` |
| [GHSA-vh95-rmgr-6w4m](https://github.com/advisories/GHSA-vh95-rmgr-6w4m) | minimist | moderate | `<0.2.1` | `>=0.2.1` | `.>express-generator>mkdirp>minimist` |
| [GHSA-f8q6-p94x-37v3](https://github.com/advisories/GHSA-f8q6-p94x-37v3) | minimatch | high | `<3.0.5` | `>=3.0.5` | `.>express-generator>minimatch` |
| [GHSA-hhhv-q57g-882q](https://github.com/advisories/GHSA-hhhv-q57g-882q) | jose | moderate | `>=3.0.0 <=4.15.4` | `>=4.15.5` | `.>newman>postman-runtime>jose` |
| [GHSA-f5x3-32g6-xq36](https://github.com/advisories/GHSA-f5x3-32g6-xq36) | tar | moderate | `<6.2.1` | `>=7.5.8` | `.>create-react-app>tar-pack>tar` |
| [GHSA-xvch-5gv4-984h](https://github.com/advisories/GHSA-xvch-5gv4-984h) | minimist | critical | `<0.2.4` | `>=0.2.4` | `.>express-generator>mkdirp>minimist` |
| [GHSA-grv7-fg5c-xmjg](https://github.com/advisories/GHSA-grv7-fg5c-xmjg) | braces | high | `<3.0.3` | `>=3.0.3` | `.>@vue/cli>vue-codemod>jscodeshift>micromatch>braces` |
| [GHSA-ghr5-ch3p-vcr6](https://github.com/advisories/GHSA-ghr5-ch3p-vcr6) | ejs | moderate | `<3.1.10` | `>=3.1.10` | `.>express-generator>ejs` |
| [GHSA-952p-6rrq-rcjv](https://github.com/advisories/GHSA-952p-6rrq-rcjv) | micromatch | moderate | `<4.0.8` | `>=4.0.8` | `.>@vue/cli>vue-codemod>jscodeshift>micromatch` |
| [GHSA-5j4c-8p2g-v4jx](https://github.com/advisories/GHSA-5j4c-8p2g-v4jx) | vue | low | `>=2.0.0-alpha.1 <3.0.0-alpha.0` | `>=3.0.0` | `.>@vue/cli>vue` |
| [GHSA-rc47-6667-2j5j](https://github.com/advisories/GHSA-rc47-6667-2j5j) | http-cache-semantics | high | `<4.1.1` | `>=4.1.1` | `.>@vue/cli>download-git-repo>download>got>cacheable-request>http-cache-semantics` |
| [GHSA-8g77-54rh-46hx](https://github.com/advisories/GHSA-8g77-54rh-46hx) | parse-git-config | high | `<=3.0.0` | `none reported` | `.>@vue/cli>@vue/cli-ui>parse-git-config` |
| [GHSA-52f5-9888-hmc6](https://github.com/advisories/GHSA-52f5-9888-hmc6) | tmp | low | `<=0.2.3` | `>=0.2.4` | `.>@vue/cli>vue-codemod>inquirer>external-editor>tmp` |
| [GHSA-554w-wpv2-vw27](https://github.com/advisories/GHSA-554w-wpv2-vw27) | node-forge | high | `<1.3.2` | `>=1.3.2` | `.>newman>postman-runtime>node-forge` |
| [GHSA-5gfm-wpxj-wjgq](https://github.com/advisories/GHSA-5gfm-wpxj-wjgq) | node-forge | high | `<1.3.2` | `>=1.3.2` | `.>newman>postman-runtime>node-forge` |
| [GHSA-65ch-62r8-g69g](https://github.com/advisories/GHSA-65ch-62r8-g69g) | node-forge | moderate | `<1.3.2` | `>=1.3.2` | `.>newman>postman-runtime>node-forge` |
| [GHSA-34x7-hfp2-rc4v](https://github.com/advisories/GHSA-34x7-hfp2-rc4v) | tar | high | `<7.5.7` | `>=7.5.8` | `.>create-react-app>tar-pack>tar` |
| [GHSA-8qq5-rm4j-mr97](https://github.com/advisories/GHSA-8qq5-rm4j-mr97) | tar | high | `<=7.5.2` | `>=7.5.8` | `.>create-react-app>tar-pack>tar` |
| [GHSA-83g3-92jg-28cx](https://github.com/advisories/GHSA-83g3-92jg-28cx) | tar | high | `<7.5.8` | `>=7.5.8` | `.>create-react-app>tar-pack>tar` |
| [GHSA-3ppc-4f35-3m26](https://github.com/advisories/GHSA-3ppc-4f35-3m26) | minimatch | high | `<3.1.3` | `>=3.1.3` | `.>express-generator>minimatch` |
| [GHSA-7r86-cg39-jmmj](https://github.com/advisories/GHSA-7r86-cg39-jmmj) | minimatch | high | `<3.1.3` | `>=3.1.3` | `.>express-generator>minimatch` |
| [GHSA-23c5-xmqv-rm74](https://github.com/advisories/GHSA-23c5-xmqv-rm74) | minimatch | high | `<3.1.4` | `>=3.1.4` | `.>express-generator>minimatch` |
| [GHSA-6rw7-vpxm-498p](https://github.com/advisories/GHSA-6rw7-vpxm-498p) | qs | moderate | `<6.14.1` | `>=6.14.1` | `.>newman>postman-runtime>postman-request>qs` |
| [GHSA-qffp-2rhf-9h96](https://github.com/advisories/GHSA-qffp-2rhf-9h96) | tar | high | `<=7.5.9` | `>=7.5.10` | `.>create-react-app>tar-pack>tar` |
| [GHSA-9ppj-qmqm-q256](https://github.com/advisories/GHSA-9ppj-qmqm-q256) | tar | high | `<=7.5.10` | `>=7.5.11` | `.>create-react-app>tar-pack>tar` |
| [GHSA-25h7-pfq9-p65f](https://github.com/advisories/GHSA-25h7-pfq9-p65f) | flatted | high | `<3.4.0` | `>=3.4.0` | `.>newman>postman-runtime>postman-sandbox>uvm>flatted` |
| [GHSA-r6q2-hw4h-h46w](https://github.com/advisories/GHSA-r6q2-hw4h-h46w) | tar | high | `<=7.5.3` | `>=7.5.8` | `.>create-react-app>tar-pack>tar` |
| [GHSA-9q82-xgwf-vj6h](https://github.com/advisories/GHSA-9q82-xgwf-vj6h) | apollo-server-core | moderate | `<=3.13.0` | `none reported` | `.>@vue/cli>@vue/cli-ui>apollo-server-express>apollo-server-core` |
| [GHSA-3mfm-83xf-c92r](https://github.com/advisories/GHSA-3mfm-83xf-c92r) | handlebars | high | `>=4.0.0 <=4.7.8` | `>=4.7.9` | `.>newman>postman-runtime>handlebars` |
| [GHSA-2w6w-674q-4c4q](https://github.com/advisories/GHSA-2w6w-674q-4c4q) | handlebars | critical | `>=4.0.0 <=4.7.8` | `>=4.7.9` | `.>newman>postman-runtime>handlebars` |
| [GHSA-2qvq-rjwj-gvw9](https://github.com/advisories/GHSA-2qvq-rjwj-gvw9) | handlebars | moderate | `>=4.0.0 <4.7.9` | `>=4.7.9` | `.>newman>postman-runtime>handlebars` |
| [GHSA-2328-f5f3-gj25](https://github.com/advisories/GHSA-2328-f5f3-gj25) | node-forge | high | `<=1.3.3` | `>=1.4.0` | `.>newman>postman-runtime>node-forge` |
| [GHSA-q67f-28xg-22rw](https://github.com/advisories/GHSA-q67f-28xg-22rw) | node-forge | high | `<1.4.0` | `>=1.4.0` | `.>newman>postman-runtime>node-forge` |
| [GHSA-5m6q-g25r-mvwx](https://github.com/advisories/GHSA-5m6q-g25r-mvwx) | node-forge | high | `<1.4.0` | `>=1.4.0` | `.>newman>postman-runtime>node-forge` |
| [GHSA-7rx3-28cr-v5wh](https://github.com/advisories/GHSA-7rx3-28cr-v5wh) | handlebars | moderate | `>=4.6.0 <=4.7.8` | `>=4.7.9` | `.>newman>postman-runtime>handlebars` |
| [GHSA-442j-39wm-28r2](https://github.com/advisories/GHSA-442j-39wm-28r2) | handlebars | low | `>=4.0.0 <=4.7.8` | `>=4.7.9` | `.>newman>postman-runtime>handlebars` |
| [GHSA-ppp5-5v6c-4jwp](https://github.com/advisories/GHSA-ppp5-5v6c-4jwp) | node-forge | high | `<1.4.0` | `>=1.4.0` | `.>newman>postman-runtime>node-forge` |
| [GHSA-xhpv-hc6g-r9c6](https://github.com/advisories/GHSA-xhpv-hc6g-r9c6) | handlebars | high | `>=4.0.0 <=4.7.8` | `>=4.7.9` | `.>newman>postman-runtime>handlebars` |
| [GHSA-9cx6-37pm-9jff](https://github.com/advisories/GHSA-9cx6-37pm-9jff) | handlebars | high | `>=4.0.0 <=4.7.8` | `>=4.7.9` | `.>newman>postman-runtime>handlebars` |
| [GHSA-r5fr-rjxr-66jc](https://github.com/advisories/GHSA-r5fr-rjxr-66jc) | lodash | high | `>=4.0.0 <=4.17.23` | `>=4.18.1` | `.>newman>postman-runtime>lodash`<br>`.>newman>postman-runtime>postman-sandbox>lodash`<br>`.>newman>postman-runtime>postman-sandbox>postman-collection>lodash`<br>`.>newman>postman-runtime>postman-collection>lodash`<br>`.>newman>postman-collection>lodash`<br>`.>newman>postman-collection-transformer>lodash`<br>`.>newman>lodash` |
| [GHSA-f23m-r3pf-42rh](https://github.com/advisories/GHSA-f23m-r3pf-42rh) | lodash | moderate | `<=4.17.23` | `>=4.18.1` | `.>newman>postman-runtime>lodash`<br>`.>newman>postman-runtime>postman-sandbox>lodash`<br>`.>newman>postman-runtime>postman-sandbox>postman-collection>lodash`<br>`.>newman>postman-runtime>postman-collection>lodash`<br>`.>newman>postman-collection>lodash`<br>`.>newman>postman-collection-transformer>lodash`<br>`.>newman>lodash` |
| [GHSA-xjpj-3mr7-gcpf](https://github.com/advisories/GHSA-xjpj-3mr7-gcpf) | handlebars | high | `>=4.0.0 <=4.7.8` | `>=4.7.9` | `.>newman>postman-runtime>handlebars` |
| [GHSA-qpx9-hpmf-5gmw](https://github.com/advisories/GHSA-qpx9-hpmf-5gmw) | underscore | high | `<=1.13.7` | `>=1.13.8` | `.>newman>postman-runtime>httpntlm>underscore` |
| [GHSA-w5hq-g745-h8pq](https://github.com/advisories/GHSA-w5hq-g745-h8pq) | uuid | moderate | `<11.1.1` | `>=11.1.1` | `.>newman>postman-runtime>serialised-error>uuid`<br>`.>newman>serialised-error>uuid`<br>`.>newman>postman-runtime>postman-request>uuid`<br>`.>newman>postman-runtime>postman-sandbox>postman-collection>uuid`<br>`.>newman>postman-runtime>postman-collection>uuid`<br>`.>newman>postman-runtime>uuid`<br>`.>newman>postman-collection>uuid`<br>`.>newman>postman-request>uuid`<br>`.>@vue/cli>@vue/cli-ui>node-notifier>uuid`<br>`.>@vue/cli>@vue/cli-ui>apollo-server-express>apollo-server-core>uuid` |
| [GHSA-q8mj-m7cp-5q26](https://github.com/advisories/GHSA-q8mj-m7cp-5q26) | qs | moderate | `>=6.11.1 <=6.15.1` | `>=6.15.2` | `.>newman>postman-request>qs` |
| [GHSA-xxjr-mmjv-4gpg](https://github.com/advisories/GHSA-xxjr-mmjv-4gpg) | lodash | moderate | `>=4.0.0 <=4.17.22` | `>=4.17.23` | `.>newman>postman-runtime>lodash`<br>`.>newman>postman-runtime>postman-sandbox>lodash`<br>`.>newman>postman-runtime>postman-sandbox>postman-collection>lodash`<br>`.>newman>postman-runtime>postman-collection>lodash`<br>`.>newman>postman-collection>lodash`<br>`.>newman>postman-collection-transformer>lodash`<br>`.>newman>lodash` |
| [GHSA-ph9p-34f9-6g65](https://github.com/advisories/GHSA-ph9p-34f9-6g65) | tmp | high | `<0.2.6` | `>=0.2.6` | `.>@vue/cli>vue-codemod>inquirer>external-editor>tmp` |
| [GHSA-vmf3-w455-68vh](https://github.com/advisories/GHSA-vmf3-w455-68vh) | tar | moderate | `<=7.5.15` | `>=7.5.16` | `.>create-react-app>tar-pack>tar` |
| [GHSA-mp2f-45pm-3cg9](https://github.com/advisories/GHSA-mp2f-45pm-3cg9) | decompress | critical | `<=4.2.1` | `none reported` | `.>@vue/cli>download-git-repo>download>decompress` |
| [GHSA-w8wr-v893-vjvp](https://github.com/advisories/GHSA-w8wr-v893-vjvp) | tar | moderate | `<=7.5.17` | `>=7.5.18` | `.>create-react-app>tar-pack>tar` |
| [GHSA-23hp-3jrh-7fpw](https://github.com/advisories/GHSA-23hp-3jrh-7fpw) | tar | critical | `<=7.5.18` | `>=7.5.19` | `.>create-react-app>tar-pack>tar` |
| [GHSA-8x88-c5mf-7j5w](https://github.com/advisories/GHSA-8x88-c5mf-7j5w) | tar | high | `<=7.5.17` | `>=7.5.18` | `.>create-react-app>tar-pack>tar` |
| [GHSA-gvwx-54wh-qm9j](https://github.com/advisories/GHSA-gvwx-54wh-qm9j) | tar | moderate | `<=7.5.16` | `>=7.5.17` | `.>create-react-app>tar-pack>tar` |
| [GHSA-h39j-r5qq-r9mm](https://github.com/advisories/GHSA-h39j-r5qq-r9mm) | decompress | moderate | `<=4.2.1` | `none reported` | `.>@vue/cli>download-git-repo>download>decompress` |
| [GHSA-jwp9-9v96-94mx](https://github.com/advisories/GHSA-jwp9-9v96-94mx) | decompress | moderate | `<=4.2.1` | `none reported` | `.>@vue/cli>download-git-repo>download>decompress` |
| [GHSA-r292-9mhp-454m](https://github.com/advisories/GHSA-r292-9mhp-454m) | tar | high | `<=7.5.20` | `>=7.5.21` | `.>create-react-app>tar-pack>tar` |
| [GHSA-vcc3-ghjq-m6fr](https://github.com/advisories/GHSA-vcc3-ghjq-m6fr) | decode-uri-component | moderate | `<=0.4.2` | `>=0.5.0` | `.>@vue/cli>download-git-repo>download>got>cacheable-request>normalize-url>query-string>decode-uri-component`<br>`.>@vue/cli>vue-codemod>jscodeshift>micromatch>extglob>expand-brackets>snapdragon>source-map-resolve>decode-uri-component`<br>`.>@vue/cli>vue-codemod>jscodeshift>micromatch>extglob>snapdragon>source-map-resolve>decode-uri-component`<br>`.>@vue/cli>vue-codemod>jscodeshift>micromatch>snapdragon>source-map-resolve>decode-uri-component`<br>`.>@vue/cli>vue-codemod>jscodeshift>micromatch>braces>snapdragon>source-map-resolve>decode-uri-component`<br>`.>@vue/cli>vue-codemod>jscodeshift>micromatch>nanomatch>snapdragon>source-map-resolve>decode-uri-component` |
| [GHSA-qxc2-j82w-r537](https://github.com/advisories/GHSA-qxc2-j82w-r537) | @faker-js/faker | high | `<=10.4.0` | `>=10.5.0` | `.>newman>postman-runtime>postman-sandbox>postman-collection>@faker-js/faker`<br>`.>newman>postman-runtime>postman-collection>@faker-js/faker`<br>`.>newman>postman-collection>@faker-js/faker` |
| [GHSA-x5fp-wj9c-mxmx](https://github.com/advisories/GHSA-x5fp-wj9c-mxmx) | qs | moderate | `>=6.14.2 <=6.15.3` | `>=6.16.0` | `.>newman>postman-request>qs` |
| [GHSA-4mjr-xmp4-gh2g](https://github.com/advisories/GHSA-4mjr-xmp4-gh2g) | qs | moderate | `>=2.2.5 <6.16.0` | `>=6.16.0` | `.>newman>postman-request>qs`<br>`.>newman>postman-runtime>postman-request>qs` |
| [GHSA-8cw4-87c7-c6xx](https://github.com/advisories/GHSA-8cw4-87c7-c6xx) | csv-parse | moderate | `<7.0.2` | `>=7.0.2` | `.>newman>csv-parse` |
| [GHSA-2883-xcg3-v3hh](https://github.com/advisories/GHSA-2883-xcg3-v3hh) | js-yaml | high | `>=4.0.0 <4.3.2` | `>=4.3.2` | `.>pm2>js-yaml` |
| [GHSA-hrh2-vp3x-79xf](https://github.com/advisories/GHSA-hrh2-vp3x-79xf) | decompress | critical | `<=4.2.1` | `none reported` | `.>@vue/cli>download-git-repo>download>decompress` |
| [GHSA-c475-qrg2-pj4r](https://github.com/advisories/GHSA-c475-qrg2-pj4r) | basic-ftp | high | `<=6.2.0` | `>=6.2.1` | `.>pm2>proxy-agent>pac-proxy-agent>get-uri>basic-ftp` |
| [GHSA-86w9-cpqp-85rv](https://github.com/advisories/GHSA-86w9-cpqp-85rv) | node-forge | high | `<=1.4.0` | `none reported` | `.>newman>postman-runtime>node-forge` |
| [GHSA-ch52-4w7c-c8xp](https://github.com/advisories/GHSA-ch52-4w7c-c8xp) | http-cache-semantics | high | `<=4.2.0` | `>=4.3.0` | `.>@vue/cli>download-git-repo>download>got>cacheable-request>http-cache-semantics` |
| [GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm) | braces | high | `<=3.0.3` | `none reported` | `.>@vue/cli>vue-codemod>jscodeshift>micromatch>braces`<br>`.>nodemon>chokidar>braces`<br>`.>@storybook/cli>@storybook/codemod>jscodeshift>micromatch>braces`<br>`.>@storybook/cli>jscodeshift>micromatch>braces`<br>`.>pm2>chokidar>braces`<br>`.>@vue/cli>vue-codemod>globby>fast-glob>micromatch>braces`<br>`.>@vue/cli>globby>fast-glob>micromatch>braces`<br>`.>@vue/cli>@vue/cli-ui>globby>fast-glob>micromatch>braces` |
| [GHSA-rf6f-7fwh-wjgh](https://github.com/advisories/GHSA-rf6f-7fwh-wjgh) | flatted | high | `<=3.4.1` | `>=3.4.2` | `.>newman>postman-runtime>postman-sandbox>uvm>flatted` |
| [GHSA-hp3w-g68c-fv3c](https://github.com/advisories/GHSA-hp3w-g68c-fv3c) | sprintf-js | moderate | `<=1.1.3` | `none reported` | `.>@vue/cli>yaml-front-matter>js-yaml>argparse>sprintf-js` |
| [GHSA-vc2v-76pw-4v95](https://github.com/advisories/GHSA-vc2v-76pw-4v95) | compression | high | `<1.8.2` | `>=1.8.2` | `.>serve>compression` |
| [GHSA-7mx3-vvmw-hjmv](https://github.com/advisories/GHSA-7mx3-vvmw-hjmv) | @graphql-tools/utils | high | `<=12.0.0` | `>=12.0.1` | `.>@vue/cli>@vue/cli-ui>@graphql-tools/schema>@graphql-tools/merge>@graphql-tools/utils`<br>`.>@vue/cli>@vue/cli-ui>@graphql-tools/schema>@graphql-tools/utils`<br>`.>@vue/cli>@vue/cli-ui>apollo-server-express>apollo-server-core>@graphql-tools/schema>@graphql-tools/merge>@graphql-tools/utils`<br>`.>@vue/cli>@vue/cli-ui>apollo-server-express>apollo-server-core>@graphql-tools/schema>@graphql-tools/utils`<br>`.>@vue/cli>@vue/cli-ui>apollo-server-express>apollo-server-core>@graphql-tools/mock>@graphql-tools/utils`<br>`.>@vue/cli>@vue/cli-ui>apollo-server-express>apollo-server-core>@graphql-tools/mock>@graphql-tools/schema>@graphql-tools/utils`<br>`.>@vue/cli>@vue/cli-ui>apollo-server-express>apollo-server-core>@graphql-tools/mock>@graphql-tools/schema>@graphql-tools/merge>@graphql-tools/utils` |
| [GHSA-xw65-4hp5-5hc7](https://github.com/advisories/GHSA-xw65-4hp5-5hc7) | handlebars | moderate | `>=4.0.0 <=4.7.9` | `>=4.7.10` | `.>newman>postman-runtime>handlebars` |
| [GHSA-8r5x-fm3f-whwj](https://github.com/advisories/GHSA-8r5x-fm3f-whwj) | handlebars | critical | `>=4.0.0 <=4.7.9` | `>=4.7.10` | `.>newman>postman-runtime>handlebars` |
| [GHSA-p8wg-vrv2-v86f](https://github.com/advisories/GHSA-p8wg-vrv2-v86f) | handlebars | critical | `>=3.0.0 <=4.7.9` | `>=4.7.10` | `.>newman>postman-runtime>handlebars` |
