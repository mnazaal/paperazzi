/*
    Minimal Zotero-shaped webEndpoint.js snapshot for pzi cookie-bridge patch
    regression tests. Mirrors the `await session.handleURL();` anchor inside
    the request handler with realistic indentation and surrounding code.
*/

var WebSession = require('./webSession');

var WebEndpoint = module.exports = {
	supportedMethods: ["POST"],
	supportedDataTypes: ["application/json", "text/plain"],

	handle: async function (req, res) {
		var data = req.body;
		if (!data.url) {
			res.status(400).send("No URL specified\n");
			return;
		}

		var session = new WebSession(data.url, data, req.headers);
		try {
			await session.handleURL();
		} catch (e) {
			res.status(500).send("An error occurred during translation.\n");
			return;
		}
	}
};
