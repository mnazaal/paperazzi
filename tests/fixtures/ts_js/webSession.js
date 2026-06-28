/*
    Minimal Zotero-shaped webSession.js snapshot for pzi cookie-bridge patch
    regression tests. Mirrors the structural anchors the patch relies on
    (the `this._cookieSandbox = ...;` assignment inside the WebSession
    constructor) with realistic indentation and surrounding code, so a drift
    in the flexible anchor regex is caught.
*/

var CookieSandbox = require('./cookieSandbox');

var WebSession = function (url, data, options, cookieSandbox) {
	this.id = Zotero.Utilities.randomString();
	this.url = url;
	this.data = data;
	this.options = options;
	this._cookieSandbox = cookieSandbox || new CookieSandbox(null, url, "", options['user-agent']);
	this._translate = null;
};

WebSession.prototype.handleURL = async function () {
	let translate = new Zotero.Translate.Web();
	translate.setHandler("translators", this._translatorsHandler.bind(this));
	translate.setCookieSandbox(this._cookieSandbox);
	return translate.getTranslators();
};

module.exports = WebSession;
