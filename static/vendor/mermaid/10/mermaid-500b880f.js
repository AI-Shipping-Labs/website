function $h(t) {
  for (var e = [], i = 1; i < arguments.length; i++)
    e[i - 1] = arguments[i];
  var r = Array.from(typeof t == "string" ? [t] : t);
  r[r.length - 1] = r[r.length - 1].replace(/\r?\n([\t ]*)$/, "");
  var n = r.reduce(function(a, l) {
    var h = l.match(/\n([\t ]+|(?!\s).)/g);
    return h ? a.concat(h.map(function(u) {
      var d, c;
      return (c = (d = u.match(/[\t ]/g)) === null || d === void 0 ? void 0 : d.length) !== null && c !== void 0 ? c : 0;
    })) : a;
  }, []);
  if (n.length) {
    var o = new RegExp(`
[	 ]{` + Math.min.apply(Math, n) + "}", "g");
    r = r.map(function(a) {
      return a.replace(o, `
`);
    });
  }
  r[0] = r[0].replace(/^\r?\n/, "");
  var s = r[0];
  return e.forEach(function(a, l) {
    var h = s.match(/(?:^|\n)( *)$/), u = h ? h[1] : "", d = a;
    typeof a == "string" && a.includes(`
`) && (d = String(a).split(`
`).map(function(c, p) {
      return p === 0 ? c : "" + u + c;
    }).join(`
`)), s += d + r[l + 1];
  }), s;
}
var Dh = typeof globalThis < "u" ? globalThis : typeof window < "u" ? window : typeof global < "u" ? global : typeof self < "u" ? self : {};
function Nh(t) {
  return t && t.__esModule && Object.prototype.hasOwnProperty.call(t, "default") ? t.default : t;
}
var ra = { exports: {} };
(function(t, e) {
  (function(i, r) {
    t.exports = r();
  })(Dh, function() {
    var i = 1e3, r = 6e4, n = 36e5, o = "millisecond", s = "second", a = "minute", l = "hour", h = "day", u = "week", d = "month", c = "quarter", p = "year", y = "date", S = "Invalid Date", M = /^(\d{4})[-/]?(\d{1,2})?[-/]?(\d{0,2})[Tt\s]*(\d{1,2})?:?(\d{1,2})?:?(\d{1,2})?[.:]?(\d+)?$/, q = /\[([^\]]+)]|Y{1,4}|M{1,4}|D{1,2}|d{1,4}|H{1,2}|h{1,2}|a|A|m{1,2}|s{1,2}|Z{1,2}|SSS/g, T = { name: "en", weekdays: "Sunday_Monday_Tuesday_Wednesday_Thursday_Friday_Saturday".split("_"), months: "January_February_March_April_May_June_July_August_September_October_November_December".split("_"), ordinal: function(L) {
      var b = ["th", "st", "nd", "rd"], C = L % 100;
      return "[" + L + (b[(C - 20) % 10] || b[C] || b[0]) + "]";
    } }, U = function(L, b, C) {
      var v = String(L);
      return !v || v.length >= b ? L : "" + Array(b + 1 - v.length).join(C) + L;
    }, W = { s: U, z: function(L) {
      var b = -L.utcOffset(), C = Math.abs(b), v = Math.floor(C / 60), x = C % 60;
      return (b <= 0 ? "+" : "-") + U(v, 2, "0") + ":" + U(x, 2, "0");
    }, m: function L(b, C) {
      if (b.date() < C.date())
        return -L(C, b);
      var v = 12 * (C.year() - b.year()) + (C.month() - b.month()), x = b.clone().add(v, d), F = C - x < 0, N = b.clone().add(v + (F ? -1 : 1), d);
      return +(-(v + (C - x) / (F ? x - N : N - x)) || 0);
    }, a: function(L) {
      return L < 0 ? Math.ceil(L) || 0 : Math.floor(L);
    }, p: function(L) {
      return { M: d, y: p, w: u, d: h, D: y, h: l, m: a, s, ms: o, Q: c }[L] || String(L || "").toLowerCase().replace(/s$/, "");
    }, u: function(L) {
      return L === void 0;
    } }, G = "en", V = {};
    V[G] = T;
    var H = "$isDayjsObject", ue = function(L) {
      return L instanceof bt || !(!L || !L[H]);
    }, re = function L(b, C, v) {
      var x;
      if (!b)
        return G;
      if (typeof b == "string") {
        var F = b.toLowerCase();
        V[F] && (x = F), C && (V[F] = C, x = F);
        var N = b.split("-");
        if (!x && N.length > 1)
          return L(N[0]);
      } else {
        var D = b.name;
        V[D] = b, x = D;
      }
      return !v && x && (G = x), x || !v && G;
    }, j = function(L, b) {
      if (ue(L))
        return L.clone();
      var C = typeof b == "object" ? b : {};
      return C.date = L, C.args = arguments, new bt(C);
    }, $ = W;
    $.l = re, $.i = ue, $.w = function(L, b) {
      return j(L, { locale: b.$L, utc: b.$u, x: b.$x, $offset: b.$offset });
    };
    var bt = function() {
      function L(C) {
        this.$L = re(C.locale, null, !0), this.parse(C), this.$x = this.$x || C.x || {}, this[H] = !0;
      }
      var b = L.prototype;
      return b.parse = function(C) {
        this.$d = function(v) {
          var x = v.date, F = v.utc;
          if (x === null)
            return /* @__PURE__ */ new Date(NaN);
          if ($.u(x))
            return /* @__PURE__ */ new Date();
          if (x instanceof Date)
            return new Date(x);
          if (typeof x == "string" && !/Z$/i.test(x)) {
            var N = x.match(M);
            if (N) {
              var D = N[2] - 1 || 0, K = (N[7] || "0").substring(0, 3);
              return F ? new Date(Date.UTC(N[1], D, N[3] || 1, N[4] || 0, N[5] || 0, N[6] || 0, K)) : new Date(N[1], D, N[3] || 1, N[4] || 0, N[5] || 0, N[6] || 0, K);
            }
          }
          return new Date(x);
        }(C), this.init();
      }, b.init = function() {
        var C = this.$d;
        this.$y = C.getFullYear(), this.$M = C.getMonth(), this.$D = C.getDate(), this.$W = C.getDay(), this.$H = C.getHours(), this.$m = C.getMinutes(), this.$s = C.getSeconds(), this.$ms = C.getMilliseconds();
      }, b.$utils = function() {
        return $;
      }, b.isValid = function() {
        return this.$d.toString() !== S;
      }, b.isSame = function(C, v) {
        var x = j(C);
        return this.startOf(v) <= x && x <= this.endOf(v);
      }, b.isAfter = function(C, v) {
        return j(C) < this.startOf(v);
      }, b.isBefore = function(C, v) {
        return this.endOf(v) < j(C);
      }, b.$g = function(C, v, x) {
        return $.u(C) ? this[v] : this.set(x, C);
      }, b.unix = function() {
        return Math.floor(this.valueOf() / 1e3);
      }, b.valueOf = function() {
        return this.$d.getTime();
      }, b.startOf = function(C, v) {
        var x = this, F = !!$.u(v) || v, N = $.p(C), D = function(Pt, nt) {
          var Ot = $.w(x.$u ? Date.UTC(x.$y, nt, Pt) : new Date(x.$y, nt, Pt), x);
          return F ? Ot : Ot.endOf(h);
        }, K = function(Pt, nt) {
          return $.w(x.toDate()[Pt].apply(x.toDate("s"), (F ? [0, 0, 0, 0] : [23, 59, 59, 999]).slice(nt)), x);
        }, P = this.$W, it = this.$M, z = this.$D, St = "set" + (this.$u ? "UTC" : "");
        switch (N) {
          case p:
            return F ? D(1, 0) : D(31, 11);
          case d:
            return F ? D(1, it) : D(0, it + 1);
          case u:
            var kt = this.$locale().weekStart || 0, gt = (P < kt ? P + 7 : P) - kt;
            return D(F ? z - gt : z + (6 - gt), it);
          case h:
          case y:
            return K(St + "Hours", 0);
          case l:
            return K(St + "Minutes", 1);
          case a:
            return K(St + "Seconds", 2);
          case s:
            return K(St + "Milliseconds", 3);
          default:
            return this.clone();
        }
      }, b.endOf = function(C) {
        return this.startOf(C, !1);
      }, b.$set = function(C, v) {
        var x, F = $.p(C), N = "set" + (this.$u ? "UTC" : ""), D = (x = {}, x[h] = N + "Date", x[y] = N + "Date", x[d] = N + "Month", x[p] = N + "FullYear", x[l] = N + "Hours", x[a] = N + "Minutes", x[s] = N + "Seconds", x[o] = N + "Milliseconds", x)[F], K = F === h ? this.$D + (v - this.$W) : v;
        if (F === d || F === p) {
          var P = this.clone().set(y, 1);
          P.$d[D](K), P.init(), this.$d = P.set(y, Math.min(this.$D, P.daysInMonth())).$d;
        } else
          D && this.$d[D](K);
        return this.init(), this;
      }, b.set = function(C, v) {
        return this.clone().$set(C, v);
      }, b.get = function(C) {
        return this[$.p(C)]();
      }, b.add = function(C, v) {
        var x, F = this;
        C = Number(C);
        var N = $.p(v), D = function(it) {
          var z = j(F);
          return $.w(z.date(z.date() + Math.round(it * C)), F);
        };
        if (N === d)
          return this.set(d, this.$M + C);
        if (N === p)
          return this.set(p, this.$y + C);
        if (N === h)
          return D(1);
        if (N === u)
          return D(7);
        var K = (x = {}, x[a] = r, x[l] = n, x[s] = i, x)[N] || 1, P = this.$d.getTime() + C * K;
        return $.w(P, this);
      }, b.subtract = function(C, v) {
        return this.add(-1 * C, v);
      }, b.format = function(C) {
        var v = this, x = this.$locale();
        if (!this.isValid())
          return x.invalidDate || S;
        var F = C || "YYYY-MM-DDTHH:mm:ssZ", N = $.z(this), D = this.$H, K = this.$m, P = this.$M, it = x.weekdays, z = x.months, St = x.meridiem, kt = function(nt, Ot, ne, vt) {
          return nt && (nt[Ot] || nt(v, F)) || ne[Ot].slice(0, vt);
        }, gt = function(nt) {
          return $.s(D % 12 || 12, nt, "0");
        }, Pt = St || function(nt, Ot, ne) {
          var vt = nt < 12 ? "AM" : "PM";
          return ne ? vt.toLowerCase() : vt;
        };
        return F.replace(q, function(nt, Ot) {
          return Ot || function(ne) {
            switch (ne) {
              case "YY":
                return String(v.$y).slice(-2);
              case "YYYY":
                return $.s(v.$y, 4, "0");
              case "M":
                return P + 1;
              case "MM":
                return $.s(P + 1, 2, "0");
              case "MMM":
                return kt(x.monthsShort, P, z, 3);
              case "MMMM":
                return kt(z, P);
              case "D":
                return v.$D;
              case "DD":
                return $.s(v.$D, 2, "0");
              case "d":
                return String(v.$W);
              case "dd":
                return kt(x.weekdaysMin, v.$W, it, 2);
              case "ddd":
                return kt(x.weekdaysShort, v.$W, it, 3);
              case "dddd":
                return it[v.$W];
              case "H":
                return String(D);
              case "HH":
                return $.s(D, 2, "0");
              case "h":
                return gt(1);
              case "hh":
                return gt(2);
              case "a":
                return Pt(D, K, !0);
              case "A":
                return Pt(D, K, !1);
              case "m":
                return String(K);
              case "mm":
                return $.s(K, 2, "0");
              case "s":
                return String(v.$s);
              case "ss":
                return $.s(v.$s, 2, "0");
              case "SSS":
                return $.s(v.$ms, 3, "0");
              case "Z":
                return N;
            }
            return null;
          }(nt) || N.replace(":", "");
        });
      }, b.utcOffset = function() {
        return 15 * -Math.round(this.$d.getTimezoneOffset() / 15);
      }, b.diff = function(C, v, x) {
        var F, N = this, D = $.p(v), K = j(C), P = (K.utcOffset() - this.utcOffset()) * r, it = this - K, z = function() {
          return $.m(N, K);
        };
        switch (D) {
          case p:
            F = z() / 12;
            break;
          case d:
            F = z();
            break;
          case c:
            F = z() / 3;
            break;
          case u:
            F = (it - P) / 6048e5;
            break;
          case h:
            F = (it - P) / 864e5;
            break;
          case l:
            F = it / n;
            break;
          case a:
            F = it / r;
            break;
          case s:
            F = it / i;
            break;
          default:
            F = it;
        }
        return x ? F : $.a(F);
      }, b.daysInMonth = function() {
        return this.endOf(d).$D;
      }, b.$locale = function() {
        return V[this.$L];
      }, b.locale = function(C, v) {
        if (!C)
          return this.$L;
        var x = this.clone(), F = re(C, v, !0);
        return F && (x.$L = F), x;
      }, b.clone = function() {
        return $.w(this.$d, this);
      }, b.toDate = function() {
        return new Date(this.valueOf());
      }, b.toJSON = function() {
        return this.isValid() ? this.toISOString() : null;
      }, b.toISOString = function() {
        return this.$d.toISOString();
      }, b.toString = function() {
        return this.$d.toUTCString();
      }, L;
    }(), Rt = bt.prototype;
    return j.prototype = Rt, [["$ms", o], ["$s", s], ["$m", a], ["$H", l], ["$W", h], ["$M", d], ["$y", p], ["$D", y]].forEach(function(L) {
      Rt[L[1]] = function(b) {
        return this.$g(b, L[0], L[1]);
      };
    }), j.extend = function(L, b) {
      return L.$i || (L(b, bt, j), L.$i = !0), j;
    }, j.locale = re, j.isDayjs = ue, j.unix = function(L) {
      return j(1e3 * L);
    }, j.en = V[G], j.Ls = V, j.p = {}, j;
  });
})(ra);
var Rh = ra.exports;
const Ph = /* @__PURE__ */ Nh(Rh), Xt = {
  trace: 0,
  debug: 1,
  info: 2,
  warn: 3,
  error: 4,
  fatal: 5
}, A = {
  trace: (...t) => {
  },
  debug: (...t) => {
  },
  info: (...t) => {
  },
  warn: (...t) => {
  },
  error: (...t) => {
  },
  fatal: (...t) => {
  }
}, Dn = function(t = "fatal") {
  let e = Xt.fatal;
  typeof t == "string" ? (t = t.toLowerCase(), t in Xt && (e = Xt[t])) : typeof t == "number" && (e = t), A.trace = () => {
  }, A.debug = () => {
  }, A.info = () => {
  }, A.warn = () => {
  }, A.error = () => {
  }, A.fatal = () => {
  }, e <= Xt.fatal && (A.fatal = console.error ? console.error.bind(console, Bt("FATAL"), "color: orange") : console.log.bind(console, "\x1B[35m", Bt("FATAL"))), e <= Xt.error && (A.error = console.error ? console.error.bind(console, Bt("ERROR"), "color: orange") : console.log.bind(console, "\x1B[31m", Bt("ERROR"))), e <= Xt.warn && (A.warn = console.warn ? console.warn.bind(console, Bt("WARN"), "color: orange") : console.log.bind(console, "\x1B[33m", Bt("WARN"))), e <= Xt.info && (A.info = console.info ? console.info.bind(console, Bt("INFO"), "color: lightblue") : console.log.bind(console, "\x1B[34m", Bt("INFO"))), e <= Xt.debug && (A.debug = console.debug ? console.debug.bind(console, Bt("DEBUG"), "color: lightgreen") : console.log.bind(console, "\x1B[32m", Bt("DEBUG"))), e <= Xt.trace && (A.trace = console.debug ? console.debug.bind(console, Bt("TRACE"), "color: lightgreen") : console.log.bind(console, "\x1B[32m", Bt("TRACE")));
}, Bt = (t) => `%c${Ph().format("ss.SSS")} : ${t} : `;
var na = {};
(function(t) {
  Object.defineProperty(t, "__esModule", { value: !0 }), t.sanitizeUrl = t.BLANK_URL = void 0;
  var e = /^([^\w]*)(javascript|data|vbscript)/im, i = /&#(\w+)(^\w|;)?/g, r = /&(newline|tab);/gi, n = /[\u0000-\u001F\u007F-\u009F\u2000-\u200D\uFEFF]/gim, o = /^.+(:|&colon;)/gim, s = [".", "/"];
  t.BLANK_URL = "about:blank";
  function a(u) {
    return s.indexOf(u[0]) > -1;
  }
  function l(u) {
    var d = u.replace(n, "");
    return d.replace(i, function(c, p) {
      return String.fromCharCode(p);
    });
  }
  function h(u) {
    if (!u)
      return t.BLANK_URL;
    var d = l(u).replace(r, "").replace(n, "").trim();
    if (!d)
      return t.BLANK_URL;
    if (a(d))
      return d;
    var c = d.match(o);
    if (!c)
      return d;
    var p = c[0];
    return e.test(p) ? t.BLANK_URL : d;
  }
  t.sanitizeUrl = h;
})(na);
var qh = { value: () => {
} };
function oa() {
  for (var t = 0, e = arguments.length, i = {}, r; t < e; ++t) {
    if (!(r = arguments[t] + "") || r in i || /[\s.]/.test(r))
      throw new Error("illegal type: " + r);
    i[r] = [];
  }
  return new zi(i);
}
function zi(t) {
  this._ = t;
}
function zh(t, e) {
  return t.trim().split(/^|\s+/).map(function(i) {
    var r = "", n = i.indexOf(".");
    if (n >= 0 && (r = i.slice(n + 1), i = i.slice(0, n)), i && !e.hasOwnProperty(i))
      throw new Error("unknown type: " + i);
    return { type: i, name: r };
  });
}
zi.prototype = oa.prototype = {
  constructor: zi,
  on: function(t, e) {
    var i = this._, r = zh(t + "", i), n, o = -1, s = r.length;
    if (arguments.length < 2) {
      for (; ++o < s; )
        if ((n = (t = r[o]).type) && (n = Wh(i[n], t.name)))
          return n;
      return;
    }
    if (e != null && typeof e != "function")
      throw new Error("invalid callback: " + e);
    for (; ++o < s; )
      if (n = (t = r[o]).type)
        i[n] = Do(i[n], t.name, e);
      else if (e == null)
        for (n in i)
          i[n] = Do(i[n], t.name, null);
    return this;
  },
  copy: function() {
    var t = {}, e = this._;
    for (var i in e)
      t[i] = e[i].slice();
    return new zi(t);
  },
  call: function(t, e) {
    if ((n = arguments.length - 2) > 0)
      for (var i = new Array(n), r = 0, n, o; r < n; ++r)
        i[r] = arguments[r + 2];
    if (!this._.hasOwnProperty(t))
      throw new Error("unknown type: " + t);
    for (o = this._[t], r = 0, n = o.length; r < n; ++r)
      o[r].value.apply(e, i);
  },
  apply: function(t, e, i) {
    if (!this._.hasOwnProperty(t))
      throw new Error("unknown type: " + t);
    for (var r = this._[t], n = 0, o = r.length; n < o; ++n)
      r[n].value.apply(e, i);
  }
};
function Wh(t, e) {
  for (var i = 0, r = t.length, n; i < r; ++i)
    if ((n = t[i]).name === e)
      return n.value;
}
function Do(t, e, i) {
  for (var r = 0, n = t.length; r < n; ++r)
    if (t[r].name === e) {
      t[r] = qh, t = t.slice(0, r).concat(t.slice(r + 1));
      break;
    }
  return i != null && t.push({ name: e, value: i }), t;
}
var dn = "http://www.w3.org/1999/xhtml";
const No = {
  svg: "http://www.w3.org/2000/svg",
  xhtml: dn,
  xlink: "http://www.w3.org/1999/xlink",
  xml: "http://www.w3.org/XML/1998/namespace",
  xmlns: "http://www.w3.org/2000/xmlns/"
};
function Cr(t) {
  var e = t += "", i = e.indexOf(":");
  return i >= 0 && (e = t.slice(0, i)) !== "xmlns" && (t = t.slice(i + 1)), No.hasOwnProperty(e) ? { space: No[e], local: t } : t;
}
function Hh(t) {
  return function() {
    var e = this.ownerDocument, i = this.namespaceURI;
    return i === dn && e.documentElement.namespaceURI === dn ? e.createElement(t) : e.createElementNS(i, t);
  };
}
function jh(t) {
  return function() {
    return this.ownerDocument.createElementNS(t.space, t.local);
  };
}
function sa(t) {
  var e = Cr(t);
  return (e.local ? jh : Hh)(e);
}
function Uh() {
}
function Nn(t) {
  return t == null ? Uh : function() {
    return this.querySelector(t);
  };
}
function Yh(t) {
  typeof t != "function" && (t = Nn(t));
  for (var e = this._groups, i = e.length, r = new Array(i), n = 0; n < i; ++n)
    for (var o = e[n], s = o.length, a = r[n] = new Array(s), l, h, u = 0; u < s; ++u)
      (l = o[u]) && (h = t.call(l, l.__data__, u, o)) && ("__data__" in l && (h.__data__ = l.__data__), a[u] = h);
  return new Tt(r, this._parents);
}
function Gh(t) {
  return t == null ? [] : Array.isArray(t) ? t : Array.from(t);
}
function Vh() {
  return [];
}
function aa(t) {
  return t == null ? Vh : function() {
    return this.querySelectorAll(t);
  };
}
function Xh(t) {
  return function() {
    return Gh(t.apply(this, arguments));
  };
}
function Kh(t) {
  typeof t == "function" ? t = Xh(t) : t = aa(t);
  for (var e = this._groups, i = e.length, r = [], n = [], o = 0; o < i; ++o)
    for (var s = e[o], a = s.length, l, h = 0; h < a; ++h)
      (l = s[h]) && (r.push(t.call(l, l.__data__, h, s)), n.push(l));
  return new Tt(r, n);
}
function la(t) {
  return function() {
    return this.matches(t);
  };
}
function ha(t) {
  return function(e) {
    return e.matches(t);
  };
}
var Zh = Array.prototype.find;
function Jh(t) {
  return function() {
    return Zh.call(this.children, t);
  };
}
function Qh() {
  return this.firstElementChild;
}
function tc(t) {
  return this.select(t == null ? Qh : Jh(typeof t == "function" ? t : ha(t)));
}
var ec = Array.prototype.filter;
function ic() {
  return Array.from(this.children);
}
function rc(t) {
  return function() {
    return ec.call(this.children, t);
  };
}
function nc(t) {
  return this.selectAll(t == null ? ic : rc(typeof t == "function" ? t : ha(t)));
}
function oc(t) {
  typeof t != "function" && (t = la(t));
  for (var e = this._groups, i = e.length, r = new Array(i), n = 0; n < i; ++n)
    for (var o = e[n], s = o.length, a = r[n] = [], l, h = 0; h < s; ++h)
      (l = o[h]) && t.call(l, l.__data__, h, o) && a.push(l);
  return new Tt(r, this._parents);
}
function ca(t) {
  return new Array(t.length);
}
function sc() {
  return new Tt(this._enter || this._groups.map(ca), this._parents);
}
function Zi(t, e) {
  this.ownerDocument = t.ownerDocument, this.namespaceURI = t.namespaceURI, this._next = null, this._parent = t, this.__data__ = e;
}
Zi.prototype = {
  constructor: Zi,
  appendChild: function(t) {
    return this._parent.insertBefore(t, this._next);
  },
  insertBefore: function(t, e) {
    return this._parent.insertBefore(t, e);
  },
  querySelector: function(t) {
    return this._parent.querySelector(t);
  },
  querySelectorAll: function(t) {
    return this._parent.querySelectorAll(t);
  }
};
function ac(t) {
  return function() {
    return t;
  };
}
function lc(t, e, i, r, n, o) {
  for (var s = 0, a, l = e.length, h = o.length; s < h; ++s)
    (a = e[s]) ? (a.__data__ = o[s], r[s] = a) : i[s] = new Zi(t, o[s]);
  for (; s < l; ++s)
    (a = e[s]) && (n[s] = a);
}
function hc(t, e, i, r, n, o, s) {
  var a, l, h = /* @__PURE__ */ new Map(), u = e.length, d = o.length, c = new Array(u), p;
  for (a = 0; a < u; ++a)
    (l = e[a]) && (c[a] = p = s.call(l, l.__data__, a, e) + "", h.has(p) ? n[a] = l : h.set(p, l));
  for (a = 0; a < d; ++a)
    p = s.call(t, o[a], a, o) + "", (l = h.get(p)) ? (r[a] = l, l.__data__ = o[a], h.delete(p)) : i[a] = new Zi(t, o[a]);
  for (a = 0; a < u; ++a)
    (l = e[a]) && h.get(c[a]) === l && (n[a] = l);
}
function cc(t) {
  return t.__data__;
}
function uc(t, e) {
  if (!arguments.length)
    return Array.from(this, cc);
  var i = e ? hc : lc, r = this._parents, n = this._groups;
  typeof t != "function" && (t = ac(t));
  for (var o = n.length, s = new Array(o), a = new Array(o), l = new Array(o), h = 0; h < o; ++h) {
    var u = r[h], d = n[h], c = d.length, p = fc(t.call(u, u && u.__data__, h, r)), y = p.length, S = a[h] = new Array(y), M = s[h] = new Array(y), q = l[h] = new Array(c);
    i(u, d, S, M, q, p, e);
    for (var T = 0, U = 0, W, G; T < y; ++T)
      if (W = S[T]) {
        for (T >= U && (U = T + 1); !(G = M[U]) && ++U < y; )
          ;
        W._next = G || null;
      }
  }
  return s = new Tt(s, r), s._enter = a, s._exit = l, s;
}
function fc(t) {
  return typeof t == "object" && "length" in t ? t : Array.from(t);
}
function dc() {
  return new Tt(this._exit || this._groups.map(ca), this._parents);
}
function pc(t, e, i) {
  var r = this.enter(), n = this, o = this.exit();
  return typeof t == "function" ? (r = t(r), r && (r = r.selection())) : r = r.append(t + ""), e != null && (n = e(n), n && (n = n.selection())), i == null ? o.remove() : i(o), r && n ? r.merge(n).order() : n;
}
function gc(t) {
  for (var e = t.selection ? t.selection() : t, i = this._groups, r = e._groups, n = i.length, o = r.length, s = Math.min(n, o), a = new Array(n), l = 0; l < s; ++l)
    for (var h = i[l], u = r[l], d = h.length, c = a[l] = new Array(d), p, y = 0; y < d; ++y)
      (p = h[y] || u[y]) && (c[y] = p);
  for (; l < n; ++l)
    a[l] = i[l];
  return new Tt(a, this._parents);
}
function mc() {
  for (var t = this._groups, e = -1, i = t.length; ++e < i; )
    for (var r = t[e], n = r.length - 1, o = r[n], s; --n >= 0; )
      (s = r[n]) && (o && s.compareDocumentPosition(o) ^ 4 && o.parentNode.insertBefore(s, o), o = s);
  return this;
}
function yc(t) {
  t || (t = _c);
  function e(d, c) {
    return d && c ? t(d.__data__, c.__data__) : !d - !c;
  }
  for (var i = this._groups, r = i.length, n = new Array(r), o = 0; o < r; ++o) {
    for (var s = i[o], a = s.length, l = n[o] = new Array(a), h, u = 0; u < a; ++u)
      (h = s[u]) && (l[u] = h);
    l.sort(e);
  }
  return new Tt(n, this._parents).order();
}
function _c(t, e) {
  return t < e ? -1 : t > e ? 1 : t >= e ? 0 : NaN;
}
function Cc() {
  var t = arguments[0];
  return arguments[0] = this, t.apply(null, arguments), this;
}
function xc() {
  return Array.from(this);
}
function bc() {
  for (var t = this._groups, e = 0, i = t.length; e < i; ++e)
    for (var r = t[e], n = 0, o = r.length; n < o; ++n) {
      var s = r[n];
      if (s)
        return s;
    }
  return null;
}
function Tc() {
  let t = 0;
  for (const e of this)
    ++t;
  return t;
}
function Sc() {
  return !this.node();
}
function kc(t) {
  for (var e = this._groups, i = 0, r = e.length; i < r; ++i)
    for (var n = e[i], o = 0, s = n.length, a; o < s; ++o)
      (a = n[o]) && t.call(a, a.__data__, o, n);
  return this;
}
function vc(t) {
  return function() {
    this.removeAttribute(t);
  };
}
function wc(t) {
  return function() {
    this.removeAttributeNS(t.space, t.local);
  };
}
function Bc(t, e) {
  return function() {
    this.setAttribute(t, e);
  };
}
function Ac(t, e) {
  return function() {
    this.setAttributeNS(t.space, t.local, e);
  };
}
function Fc(t, e) {
  return function() {
    var i = e.apply(this, arguments);
    i == null ? this.removeAttribute(t) : this.setAttribute(t, i);
  };
}
function Lc(t, e) {
  return function() {
    var i = e.apply(this, arguments);
    i == null ? this.removeAttributeNS(t.space, t.local) : this.setAttributeNS(t.space, t.local, i);
  };
}
function Ec(t, e) {
  var i = Cr(t);
  if (arguments.length < 2) {
    var r = this.node();
    return i.local ? r.getAttributeNS(i.space, i.local) : r.getAttribute(i);
  }
  return this.each((e == null ? i.local ? wc : vc : typeof e == "function" ? i.local ? Lc : Fc : i.local ? Ac : Bc)(i, e));
}
function ua(t) {
  return t.ownerDocument && t.ownerDocument.defaultView || t.document && t || t.defaultView;
}
function Oc(t) {
  return function() {
    this.style.removeProperty(t);
  };
}
function Mc(t, e, i) {
  return function() {
    this.style.setProperty(t, e, i);
  };
}
function Ic(t, e, i) {
  return function() {
    var r = e.apply(this, arguments);
    r == null ? this.style.removeProperty(t) : this.style.setProperty(t, r, i);
  };
}
function $c(t, e, i) {
  return arguments.length > 1 ? this.each((e == null ? Oc : typeof e == "function" ? Ic : Mc)(t, e, i ?? "")) : De(this.node(), t);
}
function De(t, e) {
  return t.style.getPropertyValue(e) || ua(t).getComputedStyle(t, null).getPropertyValue(e);
}
function Dc(t) {
  return function() {
    delete this[t];
  };
}
function Nc(t, e) {
  return function() {
    this[t] = e;
  };
}
function Rc(t, e) {
  return function() {
    var i = e.apply(this, arguments);
    i == null ? delete this[t] : this[t] = i;
  };
}
function Pc(t, e) {
  return arguments.length > 1 ? this.each((e == null ? Dc : typeof e == "function" ? Rc : Nc)(t, e)) : this.node()[t];
}
function fa(t) {
  return t.trim().split(/^|\s+/);
}
function Rn(t) {
  return t.classList || new da(t);
}
function da(t) {
  this._node = t, this._names = fa(t.getAttribute("class") || "");
}
da.prototype = {
  add: function(t) {
    var e = this._names.indexOf(t);
    e < 0 && (this._names.push(t), this._node.setAttribute("class", this._names.join(" ")));
  },
  remove: function(t) {
    var e = this._names.indexOf(t);
    e >= 0 && (this._names.splice(e, 1), this._node.setAttribute("class", this._names.join(" ")));
  },
  contains: function(t) {
    return this._names.indexOf(t) >= 0;
  }
};
function pa(t, e) {
  for (var i = Rn(t), r = -1, n = e.length; ++r < n; )
    i.add(e[r]);
}
function ga(t, e) {
  for (var i = Rn(t), r = -1, n = e.length; ++r < n; )
    i.remove(e[r]);
}
function qc(t) {
  return function() {
    pa(this, t);
  };
}
function zc(t) {
  return function() {
    ga(this, t);
  };
}
function Wc(t, e) {
  return function() {
    (e.apply(this, arguments) ? pa : ga)(this, t);
  };
}
function Hc(t, e) {
  var i = fa(t + "");
  if (arguments.length < 2) {
    for (var r = Rn(this.node()), n = -1, o = i.length; ++n < o; )
      if (!r.contains(i[n]))
        return !1;
    return !0;
  }
  return this.each((typeof e == "function" ? Wc : e ? qc : zc)(i, e));
}
function jc() {
  this.textContent = "";
}
function Uc(t) {
  return function() {
    this.textContent = t;
  };
}
function Yc(t) {
  return function() {
    var e = t.apply(this, arguments);
    this.textContent = e ?? "";
  };
}
function Gc(t) {
  return arguments.length ? this.each(t == null ? jc : (typeof t == "function" ? Yc : Uc)(t)) : this.node().textContent;
}
function Vc() {
  this.innerHTML = "";
}
function Xc(t) {
  return function() {
    this.innerHTML = t;
  };
}
function Kc(t) {
  return function() {
    var e = t.apply(this, arguments);
    this.innerHTML = e ?? "";
  };
}
function Zc(t) {
  return arguments.length ? this.each(t == null ? Vc : (typeof t == "function" ? Kc : Xc)(t)) : this.node().innerHTML;
}
function Jc() {
  this.nextSibling && this.parentNode.appendChild(this);
}
function Qc() {
  return this.each(Jc);
}
function tu() {
  this.previousSibling && this.parentNode.insertBefore(this, this.parentNode.firstChild);
}
function eu() {
  return this.each(tu);
}
function iu(t) {
  var e = typeof t == "function" ? t : sa(t);
  return this.select(function() {
    return this.appendChild(e.apply(this, arguments));
  });
}
function ru() {
  return null;
}
function nu(t, e) {
  var i = typeof t == "function" ? t : sa(t), r = e == null ? ru : typeof e == "function" ? e : Nn(e);
  return this.select(function() {
    return this.insertBefore(i.apply(this, arguments), r.apply(this, arguments) || null);
  });
}
function ou() {
  var t = this.parentNode;
  t && t.removeChild(this);
}
function su() {
  return this.each(ou);
}
function au() {
  var t = this.cloneNode(!1), e = this.parentNode;
  return e ? e.insertBefore(t, this.nextSibling) : t;
}
function lu() {
  var t = this.cloneNode(!0), e = this.parentNode;
  return e ? e.insertBefore(t, this.nextSibling) : t;
}
function hu(t) {
  return this.select(t ? lu : au);
}
function cu(t) {
  return arguments.length ? this.property("__data__", t) : this.node().__data__;
}
function uu(t) {
  return function(e) {
    t.call(this, e, this.__data__);
  };
}
function fu(t) {
  return t.trim().split(/^|\s+/).map(function(e) {
    var i = "", r = e.indexOf(".");
    return r >= 0 && (i = e.slice(r + 1), e = e.slice(0, r)), { type: e, name: i };
  });
}
function du(t) {
  return function() {
    var e = this.__on;
    if (e) {
      for (var i = 0, r = -1, n = e.length, o; i < n; ++i)
        o = e[i], (!t.type || o.type === t.type) && o.name === t.name ? this.removeEventListener(o.type, o.listener, o.options) : e[++r] = o;
      ++r ? e.length = r : delete this.__on;
    }
  };
}
function pu(t, e, i) {
  return function() {
    var r = this.__on, n, o = uu(e);
    if (r) {
      for (var s = 0, a = r.length; s < a; ++s)
        if ((n = r[s]).type === t.type && n.name === t.name) {
          this.removeEventListener(n.type, n.listener, n.options), this.addEventListener(n.type, n.listener = o, n.options = i), n.value = e;
          return;
        }
    }
    this.addEventListener(t.type, o, i), n = { type: t.type, name: t.name, value: e, listener: o, options: i }, r ? r.push(n) : this.__on = [n];
  };
}
function gu(t, e, i) {
  var r = fu(t + ""), n, o = r.length, s;
  if (arguments.length < 2) {
    var a = this.node().__on;
    if (a) {
      for (var l = 0, h = a.length, u; l < h; ++l)
        for (n = 0, u = a[l]; n < o; ++n)
          if ((s = r[n]).type === u.type && s.name === u.name)
            return u.value;
    }
    return;
  }
  for (a = e ? pu : du, n = 0; n < o; ++n)
    this.each(a(r[n], e, i));
  return this;
}
function ma(t, e, i) {
  var r = ua(t), n = r.CustomEvent;
  typeof n == "function" ? n = new n(e, i) : (n = r.document.createEvent("Event"), i ? (n.initEvent(e, i.bubbles, i.cancelable), n.detail = i.detail) : n.initEvent(e, !1, !1)), t.dispatchEvent(n);
}
function mu(t, e) {
  return function() {
    return ma(this, t, e);
  };
}
function yu(t, e) {
  return function() {
    return ma(this, t, e.apply(this, arguments));
  };
}
function _u(t, e) {
  return this.each((typeof e == "function" ? yu : mu)(t, e));
}
function* Cu() {
  for (var t = this._groups, e = 0, i = t.length; e < i; ++e)
    for (var r = t[e], n = 0, o = r.length, s; n < o; ++n)
      (s = r[n]) && (yield s);
}
var ya = [null];
function Tt(t, e) {
  this._groups = t, this._parents = e;
}
function Ti() {
  return new Tt([[document.documentElement]], ya);
}
function xu() {
  return this;
}
Tt.prototype = Ti.prototype = {
  constructor: Tt,
  select: Yh,
  selectAll: Kh,
  selectChild: tc,
  selectChildren: nc,
  filter: oc,
  data: uc,
  enter: sc,
  exit: dc,
  join: pc,
  merge: gc,
  selection: xu,
  order: mc,
  sort: yc,
  call: Cc,
  nodes: xc,
  node: bc,
  size: Tc,
  empty: Sc,
  each: kc,
  attr: Ec,
  style: $c,
  property: Pc,
  classed: Hc,
  text: Gc,
  html: Zc,
  raise: Qc,
  lower: eu,
  append: iu,
  insert: nu,
  remove: su,
  clone: hu,
  datum: cu,
  on: gu,
  dispatch: _u,
  [Symbol.iterator]: Cu
};
function At(t) {
  return typeof t == "string" ? new Tt([[document.querySelector(t)]], [document.documentElement]) : new Tt([[t]], ya);
}
function Pn(t, e, i) {
  t.prototype = e.prototype = i, i.constructor = t;
}
function _a(t, e) {
  var i = Object.create(t.prototype);
  for (var r in e)
    i[r] = e[r];
  return i;
}
function Si() {
}
var di = 0.7, Ji = 1 / di, $e = "\\s*([+-]?\\d+)\\s*", pi = "\\s*([+-]?(?:\\d*\\.)?\\d+(?:[eE][+-]?\\d+)?)\\s*", Wt = "\\s*([+-]?(?:\\d*\\.)?\\d+(?:[eE][+-]?\\d+)?)%\\s*", bu = /^#([0-9a-f]{3,8})$/, Tu = new RegExp(`^rgb\\(${$e},${$e},${$e}\\)$`), Su = new RegExp(`^rgb\\(${Wt},${Wt},${Wt}\\)$`), ku = new RegExp(`^rgba\\(${$e},${$e},${$e},${pi}\\)$`), vu = new RegExp(`^rgba\\(${Wt},${Wt},${Wt},${pi}\\)$`), wu = new RegExp(`^hsl\\(${pi},${Wt},${Wt}\\)$`), Bu = new RegExp(`^hsla\\(${pi},${Wt},${Wt},${pi}\\)$`), Ro = {
  aliceblue: 15792383,
  antiquewhite: 16444375,
  aqua: 65535,
  aquamarine: 8388564,
  azure: 15794175,
  beige: 16119260,
  bisque: 16770244,
  black: 0,
  blanchedalmond: 16772045,
  blue: 255,
  blueviolet: 9055202,
  brown: 10824234,
  burlywood: 14596231,
  cadetblue: 6266528,
  chartreuse: 8388352,
  chocolate: 13789470,
  coral: 16744272,
  cornflowerblue: 6591981,
  cornsilk: 16775388,
  crimson: 14423100,
  cyan: 65535,
  darkblue: 139,
  darkcyan: 35723,
  darkgoldenrod: 12092939,
  darkgray: 11119017,
  darkgreen: 25600,
  darkgrey: 11119017,
  darkkhaki: 12433259,
  darkmagenta: 9109643,
  darkolivegreen: 5597999,
  darkorange: 16747520,
  darkorchid: 10040012,
  darkred: 9109504,
  darksalmon: 15308410,
  darkseagreen: 9419919,
  darkslateblue: 4734347,
  darkslategray: 3100495,
  darkslategrey: 3100495,
  darkturquoise: 52945,
  darkviolet: 9699539,
  deeppink: 16716947,
  deepskyblue: 49151,
  dimgray: 6908265,
  dimgrey: 6908265,
  dodgerblue: 2003199,
  firebrick: 11674146,
  floralwhite: 16775920,
  forestgreen: 2263842,
  fuchsia: 16711935,
  gainsboro: 14474460,
  ghostwhite: 16316671,
  gold: 16766720,
  goldenrod: 14329120,
  gray: 8421504,
  green: 32768,
  greenyellow: 11403055,
  grey: 8421504,
  honeydew: 15794160,
  hotpink: 16738740,
  indianred: 13458524,
  indigo: 4915330,
  ivory: 16777200,
  khaki: 15787660,
  lavender: 15132410,
  lavenderblush: 16773365,
  lawngreen: 8190976,
  lemonchiffon: 16775885,
  lightblue: 11393254,
  lightcoral: 15761536,
  lightcyan: 14745599,
  lightgoldenrodyellow: 16448210,
  lightgray: 13882323,
  lightgreen: 9498256,
  lightgrey: 13882323,
  lightpink: 16758465,
  lightsalmon: 16752762,
  lightseagreen: 2142890,
  lightskyblue: 8900346,
  lightslategray: 7833753,
  lightslategrey: 7833753,
  lightsteelblue: 11584734,
  lightyellow: 16777184,
  lime: 65280,
  limegreen: 3329330,
  linen: 16445670,
  magenta: 16711935,
  maroon: 8388608,
  mediumaquamarine: 6737322,
  mediumblue: 205,
  mediumorchid: 12211667,
  mediumpurple: 9662683,
  mediumseagreen: 3978097,
  mediumslateblue: 8087790,
  mediumspringgreen: 64154,
  mediumturquoise: 4772300,
  mediumvioletred: 13047173,
  midnightblue: 1644912,
  mintcream: 16121850,
  mistyrose: 16770273,
  moccasin: 16770229,
  navajowhite: 16768685,
  navy: 128,
  oldlace: 16643558,
  olive: 8421376,
  olivedrab: 7048739,
  orange: 16753920,
  orangered: 16729344,
  orchid: 14315734,
  palegoldenrod: 15657130,
  palegreen: 10025880,
  paleturquoise: 11529966,
  palevioletred: 14381203,
  papayawhip: 16773077,
  peachpuff: 16767673,
  peru: 13468991,
  pink: 16761035,
  plum: 14524637,
  powderblue: 11591910,
  purple: 8388736,
  rebeccapurple: 6697881,
  red: 16711680,
  rosybrown: 12357519,
  royalblue: 4286945,
  saddlebrown: 9127187,
  salmon: 16416882,
  sandybrown: 16032864,
  seagreen: 3050327,
  seashell: 16774638,
  sienna: 10506797,
  silver: 12632256,
  skyblue: 8900331,
  slateblue: 6970061,
  slategray: 7372944,
  slategrey: 7372944,
  snow: 16775930,
  springgreen: 65407,
  steelblue: 4620980,
  tan: 13808780,
  teal: 32896,
  thistle: 14204888,
  tomato: 16737095,
  turquoise: 4251856,
  violet: 15631086,
  wheat: 16113331,
  white: 16777215,
  whitesmoke: 16119285,
  yellow: 16776960,
  yellowgreen: 10145074
};
Pn(Si, gi, {
  copy(t) {
    return Object.assign(new this.constructor(), this, t);
  },
  displayable() {
    return this.rgb().displayable();
  },
  hex: Po,
  // Deprecated! Use color.formatHex.
  formatHex: Po,
  formatHex8: Au,
  formatHsl: Fu,
  formatRgb: qo,
  toString: qo
});
function Po() {
  return this.rgb().formatHex();
}
function Au() {
  return this.rgb().formatHex8();
}
function Fu() {
  return Ca(this).formatHsl();
}
function qo() {
  return this.rgb().formatRgb();
}
function gi(t) {
  var e, i;
  return t = (t + "").trim().toLowerCase(), (e = bu.exec(t)) ? (i = e[1].length, e = parseInt(e[1], 16), i === 6 ? zo(e) : i === 3 ? new Ct(e >> 8 & 15 | e >> 4 & 240, e >> 4 & 15 | e & 240, (e & 15) << 4 | e & 15, 1) : i === 8 ? Oi(e >> 24 & 255, e >> 16 & 255, e >> 8 & 255, (e & 255) / 255) : i === 4 ? Oi(e >> 12 & 15 | e >> 8 & 240, e >> 8 & 15 | e >> 4 & 240, e >> 4 & 15 | e & 240, ((e & 15) << 4 | e & 15) / 255) : null) : (e = Tu.exec(t)) ? new Ct(e[1], e[2], e[3], 1) : (e = Su.exec(t)) ? new Ct(e[1] * 255 / 100, e[2] * 255 / 100, e[3] * 255 / 100, 1) : (e = ku.exec(t)) ? Oi(e[1], e[2], e[3], e[4]) : (e = vu.exec(t)) ? Oi(e[1] * 255 / 100, e[2] * 255 / 100, e[3] * 255 / 100, e[4]) : (e = wu.exec(t)) ? jo(e[1], e[2] / 100, e[3] / 100, 1) : (e = Bu.exec(t)) ? jo(e[1], e[2] / 100, e[3] / 100, e[4]) : Ro.hasOwnProperty(t) ? zo(Ro[t]) : t === "transparent" ? new Ct(NaN, NaN, NaN, 0) : null;
}
function zo(t) {
  return new Ct(t >> 16 & 255, t >> 8 & 255, t & 255, 1);
}
function Oi(t, e, i, r) {
  return r <= 0 && (t = e = i = NaN), new Ct(t, e, i, r);
}
function Lu(t) {
  return t instanceof Si || (t = gi(t)), t ? (t = t.rgb(), new Ct(t.r, t.g, t.b, t.opacity)) : new Ct();
}
function pn(t, e, i, r) {
  return arguments.length === 1 ? Lu(t) : new Ct(t, e, i, r ?? 1);
}
function Ct(t, e, i, r) {
  this.r = +t, this.g = +e, this.b = +i, this.opacity = +r;
}
Pn(Ct, pn, _a(Si, {
  brighter(t) {
    return t = t == null ? Ji : Math.pow(Ji, t), new Ct(this.r * t, this.g * t, this.b * t, this.opacity);
  },
  darker(t) {
    return t = t == null ? di : Math.pow(di, t), new Ct(this.r * t, this.g * t, this.b * t, this.opacity);
  },
  rgb() {
    return this;
  },
  clamp() {
    return new Ct(me(this.r), me(this.g), me(this.b), Qi(this.opacity));
  },
  displayable() {
    return -0.5 <= this.r && this.r < 255.5 && -0.5 <= this.g && this.g < 255.5 && -0.5 <= this.b && this.b < 255.5 && 0 <= this.opacity && this.opacity <= 1;
  },
  hex: Wo,
  // Deprecated! Use color.formatHex.
  formatHex: Wo,
  formatHex8: Eu,
  formatRgb: Ho,
  toString: Ho
}));
function Wo() {
  return `#${ge(this.r)}${ge(this.g)}${ge(this.b)}`;
}
function Eu() {
  return `#${ge(this.r)}${ge(this.g)}${ge(this.b)}${ge((isNaN(this.opacity) ? 1 : this.opacity) * 255)}`;
}
function Ho() {
  const t = Qi(this.opacity);
  return `${t === 1 ? "rgb(" : "rgba("}${me(this.r)}, ${me(this.g)}, ${me(this.b)}${t === 1 ? ")" : `, ${t})`}`;
}
function Qi(t) {
  return isNaN(t) ? 1 : Math.max(0, Math.min(1, t));
}
function me(t) {
  return Math.max(0, Math.min(255, Math.round(t) || 0));
}
function ge(t) {
  return t = me(t), (t < 16 ? "0" : "") + t.toString(16);
}
function jo(t, e, i, r) {
  return r <= 0 ? t = e = i = NaN : i <= 0 || i >= 1 ? t = e = NaN : e <= 0 && (t = NaN), new $t(t, e, i, r);
}
function Ca(t) {
  if (t instanceof $t)
    return new $t(t.h, t.s, t.l, t.opacity);
  if (t instanceof Si || (t = gi(t)), !t)
    return new $t();
  if (t instanceof $t)
    return t;
  t = t.rgb();
  var e = t.r / 255, i = t.g / 255, r = t.b / 255, n = Math.min(e, i, r), o = Math.max(e, i, r), s = NaN, a = o - n, l = (o + n) / 2;
  return a ? (e === o ? s = (i - r) / a + (i < r) * 6 : i === o ? s = (r - e) / a + 2 : s = (e - i) / a + 4, a /= l < 0.5 ? o + n : 2 - o - n, s *= 60) : a = l > 0 && l < 1 ? 0 : s, new $t(s, a, l, t.opacity);
}
function Ou(t, e, i, r) {
  return arguments.length === 1 ? Ca(t) : new $t(t, e, i, r ?? 1);
}
function $t(t, e, i, r) {
  this.h = +t, this.s = +e, this.l = +i, this.opacity = +r;
}
Pn($t, Ou, _a(Si, {
  brighter(t) {
    return t = t == null ? Ji : Math.pow(Ji, t), new $t(this.h, this.s, this.l * t, this.opacity);
  },
  darker(t) {
    return t = t == null ? di : Math.pow(di, t), new $t(this.h, this.s, this.l * t, this.opacity);
  },
  rgb() {
    var t = this.h % 360 + (this.h < 0) * 360, e = isNaN(t) || isNaN(this.s) ? 0 : this.s, i = this.l, r = i + (i < 0.5 ? i : 1 - i) * e, n = 2 * i - r;
    return new Ct(
      Kr(t >= 240 ? t - 240 : t + 120, n, r),
      Kr(t, n, r),
      Kr(t < 120 ? t + 240 : t - 120, n, r),
      this.opacity
    );
  },
  clamp() {
    return new $t(Uo(this.h), Mi(this.s), Mi(this.l), Qi(this.opacity));
  },
  displayable() {
    return (0 <= this.s && this.s <= 1 || isNaN(this.s)) && 0 <= this.l && this.l <= 1 && 0 <= this.opacity && this.opacity <= 1;
  },
  formatHsl() {
    const t = Qi(this.opacity);
    return `${t === 1 ? "hsl(" : "hsla("}${Uo(this.h)}, ${Mi(this.s) * 100}%, ${Mi(this.l) * 100}%${t === 1 ? ")" : `, ${t})`}`;
  }
}));
function Uo(t) {
  return t = (t || 0) % 360, t < 0 ? t + 360 : t;
}
function Mi(t) {
  return Math.max(0, Math.min(1, t || 0));
}
function Kr(t, e, i) {
  return (t < 60 ? e + (i - e) * t / 60 : t < 180 ? i : t < 240 ? e + (i - e) * (240 - t) / 60 : e) * 255;
}
const qn = (t) => () => t;
function xa(t, e) {
  return function(i) {
    return t + i * e;
  };
}
function Mu(t, e, i) {
  return t = Math.pow(t, i), e = Math.pow(e, i) - t, i = 1 / i, function(r) {
    return Math.pow(t + r * e, i);
  };
}
function M1(t, e) {
  var i = e - t;
  return i ? xa(t, i > 180 || i < -180 ? i - 360 * Math.round(i / 360) : i) : qn(isNaN(t) ? e : t);
}
function Iu(t) {
  return (t = +t) == 1 ? ba : function(e, i) {
    return i - e ? Mu(e, i, t) : qn(isNaN(e) ? i : e);
  };
}
function ba(t, e) {
  var i = e - t;
  return i ? xa(t, i) : qn(isNaN(t) ? e : t);
}
const Yo = function t(e) {
  var i = Iu(e);
  function r(n, o) {
    var s = i((n = pn(n)).r, (o = pn(o)).r), a = i(n.g, o.g), l = i(n.b, o.b), h = ba(n.opacity, o.opacity);
    return function(u) {
      return n.r = s(u), n.g = a(u), n.b = l(u), n.opacity = h(u), n + "";
    };
  }
  return r.gamma = t, r;
}(1);
function se(t, e) {
  return t = +t, e = +e, function(i) {
    return t * (1 - i) + e * i;
  };
}
var gn = /[-+]?(?:\d+\.?\d*|\.?\d+)(?:[eE][-+]?\d+)?/g, Zr = new RegExp(gn.source, "g");
function $u(t) {
  return function() {
    return t;
  };
}
function Du(t) {
  return function(e) {
    return t(e) + "";
  };
}
function Nu(t, e) {
  var i = gn.lastIndex = Zr.lastIndex = 0, r, n, o, s = -1, a = [], l = [];
  for (t = t + "", e = e + ""; (r = gn.exec(t)) && (n = Zr.exec(e)); )
    (o = n.index) > i && (o = e.slice(i, o), a[s] ? a[s] += o : a[++s] = o), (r = r[0]) === (n = n[0]) ? a[s] ? a[s] += n : a[++s] = n : (a[++s] = null, l.push({ i: s, x: se(r, n) })), i = Zr.lastIndex;
  return i < e.length && (o = e.slice(i), a[s] ? a[s] += o : a[++s] = o), a.length < 2 ? l[0] ? Du(l[0].x) : $u(e) : (e = l.length, function(h) {
    for (var u = 0, d; u < e; ++u)
      a[(d = l[u]).i] = d.x(h);
    return a.join("");
  });
}
var Go = 180 / Math.PI, mn = {
  translateX: 0,
  translateY: 0,
  rotate: 0,
  skewX: 0,
  scaleX: 1,
  scaleY: 1
};
function Ta(t, e, i, r, n, o) {
  var s, a, l;
  return (s = Math.sqrt(t * t + e * e)) && (t /= s, e /= s), (l = t * i + e * r) && (i -= t * l, r -= e * l), (a = Math.sqrt(i * i + r * r)) && (i /= a, r /= a, l /= a), t * r < e * i && (t = -t, e = -e, l = -l, s = -s), {
    translateX: n,
    translateY: o,
    rotate: Math.atan2(e, t) * Go,
    skewX: Math.atan(l) * Go,
    scaleX: s,
    scaleY: a
  };
}
var Ii;
function Ru(t) {
  const e = new (typeof DOMMatrix == "function" ? DOMMatrix : WebKitCSSMatrix)(t + "");
  return e.isIdentity ? mn : Ta(e.a, e.b, e.c, e.d, e.e, e.f);
}
function Pu(t) {
  return t == null || (Ii || (Ii = document.createElementNS("http://www.w3.org/2000/svg", "g")), Ii.setAttribute("transform", t), !(t = Ii.transform.baseVal.consolidate())) ? mn : (t = t.matrix, Ta(t.a, t.b, t.c, t.d, t.e, t.f));
}
function Sa(t, e, i, r) {
  function n(h) {
    return h.length ? h.pop() + " " : "";
  }
  function o(h, u, d, c, p, y) {
    if (h !== d || u !== c) {
      var S = p.push("translate(", null, e, null, i);
      y.push({ i: S - 4, x: se(h, d) }, { i: S - 2, x: se(u, c) });
    } else
      (d || c) && p.push("translate(" + d + e + c + i);
  }
  function s(h, u, d, c) {
    h !== u ? (h - u > 180 ? u += 360 : u - h > 180 && (h += 360), c.push({ i: d.push(n(d) + "rotate(", null, r) - 2, x: se(h, u) })) : u && d.push(n(d) + "rotate(" + u + r);
  }
  function a(h, u, d, c) {
    h !== u ? c.push({ i: d.push(n(d) + "skewX(", null, r) - 2, x: se(h, u) }) : u && d.push(n(d) + "skewX(" + u + r);
  }
  function l(h, u, d, c, p, y) {
    if (h !== d || u !== c) {
      var S = p.push(n(p) + "scale(", null, ",", null, ")");
      y.push({ i: S - 4, x: se(h, d) }, { i: S - 2, x: se(u, c) });
    } else
      (d !== 1 || c !== 1) && p.push(n(p) + "scale(" + d + "," + c + ")");
  }
  return function(h, u) {
    var d = [], c = [];
    return h = t(h), u = t(u), o(h.translateX, h.translateY, u.translateX, u.translateY, d, c), s(h.rotate, u.rotate, d, c), a(h.skewX, u.skewX, d, c), l(h.scaleX, h.scaleY, u.scaleX, u.scaleY, d, c), h = u = null, function(p) {
      for (var y = -1, S = c.length, M; ++y < S; )
        d[(M = c[y]).i] = M.x(p);
      return d.join("");
    };
  };
}
var qu = Sa(Ru, "px, ", "px)", "deg)"), zu = Sa(Pu, ", ", ")", ")"), Ne = 0, ri = 0, Ze = 0, ka = 1e3, tr, ni, er = 0, Ce = 0, xr = 0, mi = typeof performance == "object" && performance.now ? performance : Date, va = typeof window == "object" && window.requestAnimationFrame ? window.requestAnimationFrame.bind(window) : function(t) {
  setTimeout(t, 17);
};
function zn() {
  return Ce || (va(Wu), Ce = mi.now() + xr);
}
function Wu() {
  Ce = 0;
}
function ir() {
  this._call = this._time = this._next = null;
}
ir.prototype = wa.prototype = {
  constructor: ir,
  restart: function(t, e, i) {
    if (typeof t != "function")
      throw new TypeError("callback is not a function");
    i = (i == null ? zn() : +i) + (e == null ? 0 : +e), !this._next && ni !== this && (ni ? ni._next = this : tr = this, ni = this), this._call = t, this._time = i, yn();
  },
  stop: function() {
    this._call && (this._call = null, this._time = 1 / 0, yn());
  }
};
function wa(t, e, i) {
  var r = new ir();
  return r.restart(t, e, i), r;
}
function Hu() {
  zn(), ++Ne;
  for (var t = tr, e; t; )
    (e = Ce - t._time) >= 0 && t._call.call(void 0, e), t = t._next;
  --Ne;
}
function Vo() {
  Ce = (er = mi.now()) + xr, Ne = ri = 0;
  try {
    Hu();
  } finally {
    Ne = 0, Uu(), Ce = 0;
  }
}
function ju() {
  var t = mi.now(), e = t - er;
  e > ka && (xr -= e, er = t);
}
function Uu() {
  for (var t, e = tr, i, r = 1 / 0; e; )
    e._call ? (r > e._time && (r = e._time), t = e, e = e._next) : (i = e._next, e._next = null, e = t ? t._next = i : tr = i);
  ni = t, yn(r);
}
function yn(t) {
  if (!Ne) {
    ri && (ri = clearTimeout(ri));
    var e = t - Ce;
    e > 24 ? (t < 1 / 0 && (ri = setTimeout(Vo, t - mi.now() - xr)), Ze && (Ze = clearInterval(Ze))) : (Ze || (er = mi.now(), Ze = setInterval(ju, ka)), Ne = 1, va(Vo));
  }
}
function Xo(t, e, i) {
  var r = new ir();
  return e = e == null ? 0 : +e, r.restart((n) => {
    r.stop(), t(n + e);
  }, e, i), r;
}
var Yu = oa("start", "end", "cancel", "interrupt"), Gu = [], Ba = 0, Ko = 1, _n = 2, Wi = 3, Zo = 4, Cn = 5, Hi = 6;
function br(t, e, i, r, n, o) {
  var s = t.__transition;
  if (!s)
    t.__transition = {};
  else if (i in s)
    return;
  Vu(t, i, {
    name: e,
    index: r,
    // For context during callback.
    group: n,
    // For context during callback.
    on: Yu,
    tween: Gu,
    time: o.time,
    delay: o.delay,
    duration: o.duration,
    ease: o.ease,
    timer: null,
    state: Ba
  });
}
function Wn(t, e) {
  var i = Nt(t, e);
  if (i.state > Ba)
    throw new Error("too late; already scheduled");
  return i;
}
function Yt(t, e) {
  var i = Nt(t, e);
  if (i.state > Wi)
    throw new Error("too late; already running");
  return i;
}
function Nt(t, e) {
  var i = t.__transition;
  if (!i || !(i = i[e]))
    throw new Error("transition not found");
  return i;
}
function Vu(t, e, i) {
  var r = t.__transition, n;
  r[e] = i, i.timer = wa(o, 0, i.time);
  function o(h) {
    i.state = Ko, i.timer.restart(s, i.delay, i.time), i.delay <= h && s(h - i.delay);
  }
  function s(h) {
    var u, d, c, p;
    if (i.state !== Ko)
      return l();
    for (u in r)
      if (p = r[u], p.name === i.name) {
        if (p.state === Wi)
          return Xo(s);
        p.state === Zo ? (p.state = Hi, p.timer.stop(), p.on.call("interrupt", t, t.__data__, p.index, p.group), delete r[u]) : +u < e && (p.state = Hi, p.timer.stop(), p.on.call("cancel", t, t.__data__, p.index, p.group), delete r[u]);
      }
    if (Xo(function() {
      i.state === Wi && (i.state = Zo, i.timer.restart(a, i.delay, i.time), a(h));
    }), i.state = _n, i.on.call("start", t, t.__data__, i.index, i.group), i.state === _n) {
      for (i.state = Wi, n = new Array(c = i.tween.length), u = 0, d = -1; u < c; ++u)
        (p = i.tween[u].value.call(t, t.__data__, i.index, i.group)) && (n[++d] = p);
      n.length = d + 1;
    }
  }
  function a(h) {
    for (var u = h < i.duration ? i.ease.call(null, h / i.duration) : (i.timer.restart(l), i.state = Cn, 1), d = -1, c = n.length; ++d < c; )
      n[d].call(t, u);
    i.state === Cn && (i.on.call("end", t, t.__data__, i.index, i.group), l());
  }
  function l() {
    i.state = Hi, i.timer.stop(), delete r[e];
    for (var h in r)
      return;
    delete t.__transition;
  }
}
function Xu(t, e) {
  var i = t.__transition, r, n, o = !0, s;
  if (i) {
    e = e == null ? null : e + "";
    for (s in i) {
      if ((r = i[s]).name !== e) {
        o = !1;
        continue;
      }
      n = r.state > _n && r.state < Cn, r.state = Hi, r.timer.stop(), r.on.call(n ? "interrupt" : "cancel", t, t.__data__, r.index, r.group), delete i[s];
    }
    o && delete t.__transition;
  }
}
function Ku(t) {
  return this.each(function() {
    Xu(this, t);
  });
}
function Zu(t, e) {
  var i, r;
  return function() {
    var n = Yt(this, t), o = n.tween;
    if (o !== i) {
      r = i = o;
      for (var s = 0, a = r.length; s < a; ++s)
        if (r[s].name === e) {
          r = r.slice(), r.splice(s, 1);
          break;
        }
    }
    n.tween = r;
  };
}
function Ju(t, e, i) {
  var r, n;
  if (typeof i != "function")
    throw new Error();
  return function() {
    var o = Yt(this, t), s = o.tween;
    if (s !== r) {
      n = (r = s).slice();
      for (var a = { name: e, value: i }, l = 0, h = n.length; l < h; ++l)
        if (n[l].name === e) {
          n[l] = a;
          break;
        }
      l === h && n.push(a);
    }
    o.tween = n;
  };
}
function Qu(t, e) {
  var i = this._id;
  if (t += "", arguments.length < 2) {
    for (var r = Nt(this.node(), i).tween, n = 0, o = r.length, s; n < o; ++n)
      if ((s = r[n]).name === t)
        return s.value;
    return null;
  }
  return this.each((e == null ? Zu : Ju)(i, t, e));
}
function Hn(t, e, i) {
  var r = t._id;
  return t.each(function() {
    var n = Yt(this, r);
    (n.value || (n.value = {}))[e] = i.apply(this, arguments);
  }), function(n) {
    return Nt(n, r).value[e];
  };
}
function Aa(t, e) {
  var i;
  return (typeof e == "number" ? se : e instanceof gi ? Yo : (i = gi(e)) ? (e = i, Yo) : Nu)(t, e);
}
function tf(t) {
  return function() {
    this.removeAttribute(t);
  };
}
function ef(t) {
  return function() {
    this.removeAttributeNS(t.space, t.local);
  };
}
function rf(t, e, i) {
  var r, n = i + "", o;
  return function() {
    var s = this.getAttribute(t);
    return s === n ? null : s === r ? o : o = e(r = s, i);
  };
}
function nf(t, e, i) {
  var r, n = i + "", o;
  return function() {
    var s = this.getAttributeNS(t.space, t.local);
    return s === n ? null : s === r ? o : o = e(r = s, i);
  };
}
function of(t, e, i) {
  var r, n, o;
  return function() {
    var s, a = i(this), l;
    return a == null ? void this.removeAttribute(t) : (s = this.getAttribute(t), l = a + "", s === l ? null : s === r && l === n ? o : (n = l, o = e(r = s, a)));
  };
}
function sf(t, e, i) {
  var r, n, o;
  return function() {
    var s, a = i(this), l;
    return a == null ? void this.removeAttributeNS(t.space, t.local) : (s = this.getAttributeNS(t.space, t.local), l = a + "", s === l ? null : s === r && l === n ? o : (n = l, o = e(r = s, a)));
  };
}
function af(t, e) {
  var i = Cr(t), r = i === "transform" ? zu : Aa;
  return this.attrTween(t, typeof e == "function" ? (i.local ? sf : of)(i, r, Hn(this, "attr." + t, e)) : e == null ? (i.local ? ef : tf)(i) : (i.local ? nf : rf)(i, r, e));
}
function lf(t, e) {
  return function(i) {
    this.setAttribute(t, e.call(this, i));
  };
}
function hf(t, e) {
  return function(i) {
    this.setAttributeNS(t.space, t.local, e.call(this, i));
  };
}
function cf(t, e) {
  var i, r;
  function n() {
    var o = e.apply(this, arguments);
    return o !== r && (i = (r = o) && hf(t, o)), i;
  }
  return n._value = e, n;
}
function uf(t, e) {
  var i, r;
  function n() {
    var o = e.apply(this, arguments);
    return o !== r && (i = (r = o) && lf(t, o)), i;
  }
  return n._value = e, n;
}
function ff(t, e) {
  var i = "attr." + t;
  if (arguments.length < 2)
    return (i = this.tween(i)) && i._value;
  if (e == null)
    return this.tween(i, null);
  if (typeof e != "function")
    throw new Error();
  var r = Cr(t);
  return this.tween(i, (r.local ? cf : uf)(r, e));
}
function df(t, e) {
  return function() {
    Wn(this, t).delay = +e.apply(this, arguments);
  };
}
function pf(t, e) {
  return e = +e, function() {
    Wn(this, t).delay = e;
  };
}
function gf(t) {
  var e = this._id;
  return arguments.length ? this.each((typeof t == "function" ? df : pf)(e, t)) : Nt(this.node(), e).delay;
}
function mf(t, e) {
  return function() {
    Yt(this, t).duration = +e.apply(this, arguments);
  };
}
function yf(t, e) {
  return e = +e, function() {
    Yt(this, t).duration = e;
  };
}
function _f(t) {
  var e = this._id;
  return arguments.length ? this.each((typeof t == "function" ? mf : yf)(e, t)) : Nt(this.node(), e).duration;
}
function Cf(t, e) {
  if (typeof e != "function")
    throw new Error();
  return function() {
    Yt(this, t).ease = e;
  };
}
function xf(t) {
  var e = this._id;
  return arguments.length ? this.each(Cf(e, t)) : Nt(this.node(), e).ease;
}
function bf(t, e) {
  return function() {
    var i = e.apply(this, arguments);
    if (typeof i != "function")
      throw new Error();
    Yt(this, t).ease = i;
  };
}
function Tf(t) {
  if (typeof t != "function")
    throw new Error();
  return this.each(bf(this._id, t));
}
function Sf(t) {
  typeof t != "function" && (t = la(t));
  for (var e = this._groups, i = e.length, r = new Array(i), n = 0; n < i; ++n)
    for (var o = e[n], s = o.length, a = r[n] = [], l, h = 0; h < s; ++h)
      (l = o[h]) && t.call(l, l.__data__, h, o) && a.push(l);
  return new ee(r, this._parents, this._name, this._id);
}
function kf(t) {
  if (t._id !== this._id)
    throw new Error();
  for (var e = this._groups, i = t._groups, r = e.length, n = i.length, o = Math.min(r, n), s = new Array(r), a = 0; a < o; ++a)
    for (var l = e[a], h = i[a], u = l.length, d = s[a] = new Array(u), c, p = 0; p < u; ++p)
      (c = l[p] || h[p]) && (d[p] = c);
  for (; a < r; ++a)
    s[a] = e[a];
  return new ee(s, this._parents, this._name, this._id);
}
function vf(t) {
  return (t + "").trim().split(/^|\s+/).every(function(e) {
    var i = e.indexOf(".");
    return i >= 0 && (e = e.slice(0, i)), !e || e === "start";
  });
}
function wf(t, e, i) {
  var r, n, o = vf(e) ? Wn : Yt;
  return function() {
    var s = o(this, t), a = s.on;
    a !== r && (n = (r = a).copy()).on(e, i), s.on = n;
  };
}
function Bf(t, e) {
  var i = this._id;
  return arguments.length < 2 ? Nt(this.node(), i).on.on(t) : this.each(wf(i, t, e));
}
function Af(t) {
  return function() {
    var e = this.parentNode;
    for (var i in this.__transition)
      if (+i !== t)
        return;
    e && e.removeChild(this);
  };
}
function Ff() {
  return this.on("end.remove", Af(this._id));
}
function Lf(t) {
  var e = this._name, i = this._id;
  typeof t != "function" && (t = Nn(t));
  for (var r = this._groups, n = r.length, o = new Array(n), s = 0; s < n; ++s)
    for (var a = r[s], l = a.length, h = o[s] = new Array(l), u, d, c = 0; c < l; ++c)
      (u = a[c]) && (d = t.call(u, u.__data__, c, a)) && ("__data__" in u && (d.__data__ = u.__data__), h[c] = d, br(h[c], e, i, c, h, Nt(u, i)));
  return new ee(o, this._parents, e, i);
}
function Ef(t) {
  var e = this._name, i = this._id;
  typeof t != "function" && (t = aa(t));
  for (var r = this._groups, n = r.length, o = [], s = [], a = 0; a < n; ++a)
    for (var l = r[a], h = l.length, u, d = 0; d < h; ++d)
      if (u = l[d]) {
        for (var c = t.call(u, u.__data__, d, l), p, y = Nt(u, i), S = 0, M = c.length; S < M; ++S)
          (p = c[S]) && br(p, e, i, S, c, y);
        o.push(c), s.push(u);
      }
  return new ee(o, s, e, i);
}
var Of = Ti.prototype.constructor;
function Mf() {
  return new Of(this._groups, this._parents);
}
function If(t, e) {
  var i, r, n;
  return function() {
    var o = De(this, t), s = (this.style.removeProperty(t), De(this, t));
    return o === s ? null : o === i && s === r ? n : n = e(i = o, r = s);
  };
}
function Fa(t) {
  return function() {
    this.style.removeProperty(t);
  };
}
function $f(t, e, i) {
  var r, n = i + "", o;
  return function() {
    var s = De(this, t);
    return s === n ? null : s === r ? o : o = e(r = s, i);
  };
}
function Df(t, e, i) {
  var r, n, o;
  return function() {
    var s = De(this, t), a = i(this), l = a + "";
    return a == null && (l = a = (this.style.removeProperty(t), De(this, t))), s === l ? null : s === r && l === n ? o : (n = l, o = e(r = s, a));
  };
}
function Nf(t, e) {
  var i, r, n, o = "style." + e, s = "end." + o, a;
  return function() {
    var l = Yt(this, t), h = l.on, u = l.value[o] == null ? a || (a = Fa(e)) : void 0;
    (h !== i || n !== u) && (r = (i = h).copy()).on(s, n = u), l.on = r;
  };
}
function Rf(t, e, i) {
  var r = (t += "") == "transform" ? qu : Aa;
  return e == null ? this.styleTween(t, If(t, r)).on("end.style." + t, Fa(t)) : typeof e == "function" ? this.styleTween(t, Df(t, r, Hn(this, "style." + t, e))).each(Nf(this._id, t)) : this.styleTween(t, $f(t, r, e), i).on("end.style." + t, null);
}
function Pf(t, e, i) {
  return function(r) {
    this.style.setProperty(t, e.call(this, r), i);
  };
}
function qf(t, e, i) {
  var r, n;
  function o() {
    var s = e.apply(this, arguments);
    return s !== n && (r = (n = s) && Pf(t, s, i)), r;
  }
  return o._value = e, o;
}
function zf(t, e, i) {
  var r = "style." + (t += "");
  if (arguments.length < 2)
    return (r = this.tween(r)) && r._value;
  if (e == null)
    return this.tween(r, null);
  if (typeof e != "function")
    throw new Error();
  return this.tween(r, qf(t, e, i ?? ""));
}
function Wf(t) {
  return function() {
    this.textContent = t;
  };
}
function Hf(t) {
  return function() {
    var e = t(this);
    this.textContent = e ?? "";
  };
}
function jf(t) {
  return this.tween("text", typeof t == "function" ? Hf(Hn(this, "text", t)) : Wf(t == null ? "" : t + ""));
}
function Uf(t) {
  return function(e) {
    this.textContent = t.call(this, e);
  };
}
function Yf(t) {
  var e, i;
  function r() {
    var n = t.apply(this, arguments);
    return n !== i && (e = (i = n) && Uf(n)), e;
  }
  return r._value = t, r;
}
function Gf(t) {
  var e = "text";
  if (arguments.length < 1)
    return (e = this.tween(e)) && e._value;
  if (t == null)
    return this.tween(e, null);
  if (typeof t != "function")
    throw new Error();
  return this.tween(e, Yf(t));
}
function Vf() {
  for (var t = this._name, e = this._id, i = La(), r = this._groups, n = r.length, o = 0; o < n; ++o)
    for (var s = r[o], a = s.length, l, h = 0; h < a; ++h)
      if (l = s[h]) {
        var u = Nt(l, e);
        br(l, t, i, h, s, {
          time: u.time + u.delay + u.duration,
          delay: 0,
          duration: u.duration,
          ease: u.ease
        });
      }
  return new ee(r, this._parents, t, i);
}
function Xf() {
  var t, e, i = this, r = i._id, n = i.size();
  return new Promise(function(o, s) {
    var a = { value: s }, l = { value: function() {
      --n === 0 && o();
    } };
    i.each(function() {
      var h = Yt(this, r), u = h.on;
      u !== t && (e = (t = u).copy(), e._.cancel.push(a), e._.interrupt.push(a), e._.end.push(l)), h.on = e;
    }), n === 0 && o();
  });
}
var Kf = 0;
function ee(t, e, i, r) {
  this._groups = t, this._parents = e, this._name = i, this._id = r;
}
function La() {
  return ++Kf;
}
var Kt = Ti.prototype;
ee.prototype = {
  constructor: ee,
  select: Lf,
  selectAll: Ef,
  selectChild: Kt.selectChild,
  selectChildren: Kt.selectChildren,
  filter: Sf,
  merge: kf,
  selection: Mf,
  transition: Vf,
  call: Kt.call,
  nodes: Kt.nodes,
  node: Kt.node,
  size: Kt.size,
  empty: Kt.empty,
  each: Kt.each,
  on: Bf,
  attr: af,
  attrTween: ff,
  style: Rf,
  styleTween: zf,
  text: jf,
  textTween: Gf,
  remove: Ff,
  tween: Qu,
  delay: gf,
  duration: _f,
  ease: xf,
  easeVarying: Tf,
  end: Xf,
  [Symbol.iterator]: Kt[Symbol.iterator]
};
function Zf(t) {
  return ((t *= 2) <= 1 ? t * t * t : (t -= 2) * t * t + 2) / 2;
}
var Jf = {
  time: null,
  // Set on use.
  delay: 0,
  duration: 250,
  ease: Zf
};
function Qf(t, e) {
  for (var i; !(i = t.__transition) || !(i = i[e]); )
    if (!(t = t.parentNode))
      throw new Error(`transition ${e} not found`);
  return i;
}
function td(t) {
  var e, i;
  t instanceof ee ? (e = t._id, t = t._name) : (e = La(), (i = Jf).time = zn(), t = t == null ? null : t + "");
  for (var r = this._groups, n = r.length, o = 0; o < n; ++o)
    for (var s = r[o], a = s.length, l, h = 0; h < a; ++h)
      (l = s[h]) && br(l, t, e, h, s, i || Qf(l, e));
  return new ee(r, this._parents, t, e);
}
Ti.prototype.interrupt = Ku;
Ti.prototype.transition = td;
const I1 = Math.abs, $1 = Math.atan2, D1 = Math.cos, N1 = Math.max, R1 = Math.min, P1 = Math.sin, q1 = Math.sqrt, Jo = 1e-12, jn = Math.PI, Qo = jn / 2, z1 = 2 * jn;
function W1(t) {
  return t > 1 ? 0 : t < -1 ? jn : Math.acos(t);
}
function H1(t) {
  return t >= 1 ? Qo : t <= -1 ? -Qo : Math.asin(t);
}
function Ea(t) {
  this._context = t;
}
Ea.prototype = {
  areaStart: function() {
    this._line = 0;
  },
  areaEnd: function() {
    this._line = NaN;
  },
  lineStart: function() {
    this._point = 0;
  },
  lineEnd: function() {
    (this._line || this._line !== 0 && this._point === 1) && this._context.closePath(), this._line = 1 - this._line;
  },
  point: function(t, e) {
    switch (t = +t, e = +e, this._point) {
      case 0:
        this._point = 1, this._line ? this._context.lineTo(t, e) : this._context.moveTo(t, e);
        break;
      case 1:
        this._point = 2;
      default:
        this._context.lineTo(t, e);
        break;
    }
  }
};
function ed(t) {
  return new Ea(t);
}
class Oa {
  constructor(e, i) {
    this._context = e, this._x = i;
  }
  areaStart() {
    this._line = 0;
  }
  areaEnd() {
    this._line = NaN;
  }
  lineStart() {
    this._point = 0;
  }
  lineEnd() {
    (this._line || this._line !== 0 && this._point === 1) && this._context.closePath(), this._line = 1 - this._line;
  }
  point(e, i) {
    switch (e = +e, i = +i, this._point) {
      case 0: {
        this._point = 1, this._line ? this._context.lineTo(e, i) : this._context.moveTo(e, i);
        break;
      }
      case 1:
        this._point = 2;
      default: {
        this._x ? this._context.bezierCurveTo(this._x0 = (this._x0 + e) / 2, this._y0, this._x0, i, e, i) : this._context.bezierCurveTo(this._x0, this._y0 = (this._y0 + i) / 2, e, this._y0, e, i);
        break;
      }
    }
    this._x0 = e, this._y0 = i;
  }
}
function id(t) {
  return new Oa(t, !0);
}
function rd(t) {
  return new Oa(t, !1);
}
function le() {
}
function rr(t, e, i) {
  t._context.bezierCurveTo(
    (2 * t._x0 + t._x1) / 3,
    (2 * t._y0 + t._y1) / 3,
    (t._x0 + 2 * t._x1) / 3,
    (t._y0 + 2 * t._y1) / 3,
    (t._x0 + 4 * t._x1 + e) / 6,
    (t._y0 + 4 * t._y1 + i) / 6
  );
}
function Tr(t) {
  this._context = t;
}
Tr.prototype = {
  areaStart: function() {
    this._line = 0;
  },
  areaEnd: function() {
    this._line = NaN;
  },
  lineStart: function() {
    this._x0 = this._x1 = this._y0 = this._y1 = NaN, this._point = 0;
  },
  lineEnd: function() {
    switch (this._point) {
      case 3:
        rr(this, this._x1, this._y1);
      case 2:
        this._context.lineTo(this._x1, this._y1);
        break;
    }
    (this._line || this._line !== 0 && this._point === 1) && this._context.closePath(), this._line = 1 - this._line;
  },
  point: function(t, e) {
    switch (t = +t, e = +e, this._point) {
      case 0:
        this._point = 1, this._line ? this._context.lineTo(t, e) : this._context.moveTo(t, e);
        break;
      case 1:
        this._point = 2;
        break;
      case 2:
        this._point = 3, this._context.lineTo((5 * this._x0 + this._x1) / 6, (5 * this._y0 + this._y1) / 6);
      default:
        rr(this, t, e);
        break;
    }
    this._x0 = this._x1, this._x1 = t, this._y0 = this._y1, this._y1 = e;
  }
};
function nd(t) {
  return new Tr(t);
}
function Ma(t) {
  this._context = t;
}
Ma.prototype = {
  areaStart: le,
  areaEnd: le,
  lineStart: function() {
    this._x0 = this._x1 = this._x2 = this._x3 = this._x4 = this._y0 = this._y1 = this._y2 = this._y3 = this._y4 = NaN, this._point = 0;
  },
  lineEnd: function() {
    switch (this._point) {
      case 1: {
        this._context.moveTo(this._x2, this._y2), this._context.closePath();
        break;
      }
      case 2: {
        this._context.moveTo((this._x2 + 2 * this._x3) / 3, (this._y2 + 2 * this._y3) / 3), this._context.lineTo((this._x3 + 2 * this._x2) / 3, (this._y3 + 2 * this._y2) / 3), this._context.closePath();
        break;
      }
      case 3: {
        this.point(this._x2, this._y2), this.point(this._x3, this._y3), this.point(this._x4, this._y4);
        break;
      }
    }
  },
  point: function(t, e) {
    switch (t = +t, e = +e, this._point) {
      case 0:
        this._point = 1, this._x2 = t, this._y2 = e;
        break;
      case 1:
        this._point = 2, this._x3 = t, this._y3 = e;
        break;
      case 2:
        this._point = 3, this._x4 = t, this._y4 = e, this._context.moveTo((this._x0 + 4 * this._x1 + t) / 6, (this._y0 + 4 * this._y1 + e) / 6);
        break;
      default:
        rr(this, t, e);
        break;
    }
    this._x0 = this._x1, this._x1 = t, this._y0 = this._y1, this._y1 = e;
  }
};
function od(t) {
  return new Ma(t);
}
function Ia(t) {
  this._context = t;
}
Ia.prototype = {
  areaStart: function() {
    this._line = 0;
  },
  areaEnd: function() {
    this._line = NaN;
  },
  lineStart: function() {
    this._x0 = this._x1 = this._y0 = this._y1 = NaN, this._point = 0;
  },
  lineEnd: function() {
    (this._line || this._line !== 0 && this._point === 3) && this._context.closePath(), this._line = 1 - this._line;
  },
  point: function(t, e) {
    switch (t = +t, e = +e, this._point) {
      case 0:
        this._point = 1;
        break;
      case 1:
        this._point = 2;
        break;
      case 2:
        this._point = 3;
        var i = (this._x0 + 4 * this._x1 + t) / 6, r = (this._y0 + 4 * this._y1 + e) / 6;
        this._line ? this._context.lineTo(i, r) : this._context.moveTo(i, r);
        break;
      case 3:
        this._point = 4;
      default:
        rr(this, t, e);
        break;
    }
    this._x0 = this._x1, this._x1 = t, this._y0 = this._y1, this._y1 = e;
  }
};
function sd(t) {
  return new Ia(t);
}
function $a(t, e) {
  this._basis = new Tr(t), this._beta = e;
}
$a.prototype = {
  lineStart: function() {
    this._x = [], this._y = [], this._basis.lineStart();
  },
  lineEnd: function() {
    var t = this._x, e = this._y, i = t.length - 1;
    if (i > 0)
      for (var r = t[0], n = e[0], o = t[i] - r, s = e[i] - n, a = -1, l; ++a <= i; )
        l = a / i, this._basis.point(
          this._beta * t[a] + (1 - this._beta) * (r + l * o),
          this._beta * e[a] + (1 - this._beta) * (n + l * s)
        );
    this._x = this._y = null, this._basis.lineEnd();
  },
  point: function(t, e) {
    this._x.push(+t), this._y.push(+e);
  }
};
const ad = function t(e) {
  function i(r) {
    return e === 1 ? new Tr(r) : new $a(r, e);
  }
  return i.beta = function(r) {
    return t(+r);
  }, i;
}(0.85);
function nr(t, e, i) {
  t._context.bezierCurveTo(
    t._x1 + t._k * (t._x2 - t._x0),
    t._y1 + t._k * (t._y2 - t._y0),
    t._x2 + t._k * (t._x1 - e),
    t._y2 + t._k * (t._y1 - i),
    t._x2,
    t._y2
  );
}
function Un(t, e) {
  this._context = t, this._k = (1 - e) / 6;
}
Un.prototype = {
  areaStart: function() {
    this._line = 0;
  },
  areaEnd: function() {
    this._line = NaN;
  },
  lineStart: function() {
    this._x0 = this._x1 = this._x2 = this._y0 = this._y1 = this._y2 = NaN, this._point = 0;
  },
  lineEnd: function() {
    switch (this._point) {
      case 2:
        this._context.lineTo(this._x2, this._y2);
        break;
      case 3:
        nr(this, this._x1, this._y1);
        break;
    }
    (this._line || this._line !== 0 && this._point === 1) && this._context.closePath(), this._line = 1 - this._line;
  },
  point: function(t, e) {
    switch (t = +t, e = +e, this._point) {
      case 0:
        this._point = 1, this._line ? this._context.lineTo(t, e) : this._context.moveTo(t, e);
        break;
      case 1:
        this._point = 2, this._x1 = t, this._y1 = e;
        break;
      case 2:
        this._point = 3;
      default:
        nr(this, t, e);
        break;
    }
    this._x0 = this._x1, this._x1 = this._x2, this._x2 = t, this._y0 = this._y1, this._y1 = this._y2, this._y2 = e;
  }
};
const ld = function t(e) {
  function i(r) {
    return new Un(r, e);
  }
  return i.tension = function(r) {
    return t(+r);
  }, i;
}(0);
function Yn(t, e) {
  this._context = t, this._k = (1 - e) / 6;
}
Yn.prototype = {
  areaStart: le,
  areaEnd: le,
  lineStart: function() {
    this._x0 = this._x1 = this._x2 = this._x3 = this._x4 = this._x5 = this._y0 = this._y1 = this._y2 = this._y3 = this._y4 = this._y5 = NaN, this._point = 0;
  },
  lineEnd: function() {
    switch (this._point) {
      case 1: {
        this._context.moveTo(this._x3, this._y3), this._context.closePath();
        break;
      }
      case 2: {
        this._context.lineTo(this._x3, this._y3), this._context.closePath();
        break;
      }
      case 3: {
        this.point(this._x3, this._y3), this.point(this._x4, this._y4), this.point(this._x5, this._y5);
        break;
      }
    }
  },
  point: function(t, e) {
    switch (t = +t, e = +e, this._point) {
      case 0:
        this._point = 1, this._x3 = t, this._y3 = e;
        break;
      case 1:
        this._point = 2, this._context.moveTo(this._x4 = t, this._y4 = e);
        break;
      case 2:
        this._point = 3, this._x5 = t, this._y5 = e;
        break;
      default:
        nr(this, t, e);
        break;
    }
    this._x0 = this._x1, this._x1 = this._x2, this._x2 = t, this._y0 = this._y1, this._y1 = this._y2, this._y2 = e;
  }
};
const hd = function t(e) {
  function i(r) {
    return new Yn(r, e);
  }
  return i.tension = function(r) {
    return t(+r);
  }, i;
}(0);
function Gn(t, e) {
  this._context = t, this._k = (1 - e) / 6;
}
Gn.prototype = {
  areaStart: function() {
    this._line = 0;
  },
  areaEnd: function() {
    this._line = NaN;
  },
  lineStart: function() {
    this._x0 = this._x1 = this._x2 = this._y0 = this._y1 = this._y2 = NaN, this._point = 0;
  },
  lineEnd: function() {
    (this._line || this._line !== 0 && this._point === 3) && this._context.closePath(), this._line = 1 - this._line;
  },
  point: function(t, e) {
    switch (t = +t, e = +e, this._point) {
      case 0:
        this._point = 1;
        break;
      case 1:
        this._point = 2;
        break;
      case 2:
        this._point = 3, this._line ? this._context.lineTo(this._x2, this._y2) : this._context.moveTo(this._x2, this._y2);
        break;
      case 3:
        this._point = 4;
      default:
        nr(this, t, e);
        break;
    }
    this._x0 = this._x1, this._x1 = this._x2, this._x2 = t, this._y0 = this._y1, this._y1 = this._y2, this._y2 = e;
  }
};
const cd = function t(e) {
  function i(r) {
    return new Gn(r, e);
  }
  return i.tension = function(r) {
    return t(+r);
  }, i;
}(0);
function Vn(t, e, i) {
  var r = t._x1, n = t._y1, o = t._x2, s = t._y2;
  if (t._l01_a > Jo) {
    var a = 2 * t._l01_2a + 3 * t._l01_a * t._l12_a + t._l12_2a, l = 3 * t._l01_a * (t._l01_a + t._l12_a);
    r = (r * a - t._x0 * t._l12_2a + t._x2 * t._l01_2a) / l, n = (n * a - t._y0 * t._l12_2a + t._y2 * t._l01_2a) / l;
  }
  if (t._l23_a > Jo) {
    var h = 2 * t._l23_2a + 3 * t._l23_a * t._l12_a + t._l12_2a, u = 3 * t._l23_a * (t._l23_a + t._l12_a);
    o = (o * h + t._x1 * t._l23_2a - e * t._l12_2a) / u, s = (s * h + t._y1 * t._l23_2a - i * t._l12_2a) / u;
  }
  t._context.bezierCurveTo(r, n, o, s, t._x2, t._y2);
}
function Da(t, e) {
  this._context = t, this._alpha = e;
}
Da.prototype = {
  areaStart: function() {
    this._line = 0;
  },
  areaEnd: function() {
    this._line = NaN;
  },
  lineStart: function() {
    this._x0 = this._x1 = this._x2 = this._y0 = this._y1 = this._y2 = NaN, this._l01_a = this._l12_a = this._l23_a = this._l01_2a = this._l12_2a = this._l23_2a = this._point = 0;
  },
  lineEnd: function() {
    switch (this._point) {
      case 2:
        this._context.lineTo(this._x2, this._y2);
        break;
      case 3:
        this.point(this._x2, this._y2);
        break;
    }
    (this._line || this._line !== 0 && this._point === 1) && this._context.closePath(), this._line = 1 - this._line;
  },
  point: function(t, e) {
    if (t = +t, e = +e, this._point) {
      var i = this._x2 - t, r = this._y2 - e;
      this._l23_a = Math.sqrt(this._l23_2a = Math.pow(i * i + r * r, this._alpha));
    }
    switch (this._point) {
      case 0:
        this._point = 1, this._line ? this._context.lineTo(t, e) : this._context.moveTo(t, e);
        break;
      case 1:
        this._point = 2;
        break;
      case 2:
        this._point = 3;
      default:
        Vn(this, t, e);
        break;
    }
    this._l01_a = this._l12_a, this._l12_a = this._l23_a, this._l01_2a = this._l12_2a, this._l12_2a = this._l23_2a, this._x0 = this._x1, this._x1 = this._x2, this._x2 = t, this._y0 = this._y1, this._y1 = this._y2, this._y2 = e;
  }
};
const ud = function t(e) {
  function i(r) {
    return e ? new Da(r, e) : new Un(r, 0);
  }
  return i.alpha = function(r) {
    return t(+r);
  }, i;
}(0.5);
function Na(t, e) {
  this._context = t, this._alpha = e;
}
Na.prototype = {
  areaStart: le,
  areaEnd: le,
  lineStart: function() {
    this._x0 = this._x1 = this._x2 = this._x3 = this._x4 = this._x5 = this._y0 = this._y1 = this._y2 = this._y3 = this._y4 = this._y5 = NaN, this._l01_a = this._l12_a = this._l23_a = this._l01_2a = this._l12_2a = this._l23_2a = this._point = 0;
  },
  lineEnd: function() {
    switch (this._point) {
      case 1: {
        this._context.moveTo(this._x3, this._y3), this._context.closePath();
        break;
      }
      case 2: {
        this._context.lineTo(this._x3, this._y3), this._context.closePath();
        break;
      }
      case 3: {
        this.point(this._x3, this._y3), this.point(this._x4, this._y4), this.point(this._x5, this._y5);
        break;
      }
    }
  },
  point: function(t, e) {
    if (t = +t, e = +e, this._point) {
      var i = this._x2 - t, r = this._y2 - e;
      this._l23_a = Math.sqrt(this._l23_2a = Math.pow(i * i + r * r, this._alpha));
    }
    switch (this._point) {
      case 0:
        this._point = 1, this._x3 = t, this._y3 = e;
        break;
      case 1:
        this._point = 2, this._context.moveTo(this._x4 = t, this._y4 = e);
        break;
      case 2:
        this._point = 3, this._x5 = t, this._y5 = e;
        break;
      default:
        Vn(this, t, e);
        break;
    }
    this._l01_a = this._l12_a, this._l12_a = this._l23_a, this._l01_2a = this._l12_2a, this._l12_2a = this._l23_2a, this._x0 = this._x1, this._x1 = this._x2, this._x2 = t, this._y0 = this._y1, this._y1 = this._y2, this._y2 = e;
  }
};
const fd = function t(e) {
  function i(r) {
    return e ? new Na(r, e) : new Yn(r, 0);
  }
  return i.alpha = function(r) {
    return t(+r);
  }, i;
}(0.5);
function Ra(t, e) {
  this._context = t, this._alpha = e;
}
Ra.prototype = {
  areaStart: function() {
    this._line = 0;
  },
  areaEnd: function() {
    this._line = NaN;
  },
  lineStart: function() {
    this._x0 = this._x1 = this._x2 = this._y0 = this._y1 = this._y2 = NaN, this._l01_a = this._l12_a = this._l23_a = this._l01_2a = this._l12_2a = this._l23_2a = this._point = 0;
  },
  lineEnd: function() {
    (this._line || this._line !== 0 && this._point === 3) && this._context.closePath(), this._line = 1 - this._line;
  },
  point: function(t, e) {
    if (t = +t, e = +e, this._point) {
      var i = this._x2 - t, r = this._y2 - e;
      this._l23_a = Math.sqrt(this._l23_2a = Math.pow(i * i + r * r, this._alpha));
    }
    switch (this._point) {
      case 0:
        this._point = 1;
        break;
      case 1:
        this._point = 2;
        break;
      case 2:
        this._point = 3, this._line ? this._context.lineTo(this._x2, this._y2) : this._context.moveTo(this._x2, this._y2);
        break;
      case 3:
        this._point = 4;
      default:
        Vn(this, t, e);
        break;
    }
    this._l01_a = this._l12_a, this._l12_a = this._l23_a, this._l01_2a = this._l12_2a, this._l12_2a = this._l23_2a, this._x0 = this._x1, this._x1 = this._x2, this._x2 = t, this._y0 = this._y1, this._y1 = this._y2, this._y2 = e;
  }
};
const dd = function t(e) {
  function i(r) {
    return e ? new Ra(r, e) : new Gn(r, 0);
  }
  return i.alpha = function(r) {
    return t(+r);
  }, i;
}(0.5);
function Pa(t) {
  this._context = t;
}
Pa.prototype = {
  areaStart: le,
  areaEnd: le,
  lineStart: function() {
    this._point = 0;
  },
  lineEnd: function() {
    this._point && this._context.closePath();
  },
  point: function(t, e) {
    t = +t, e = +e, this._point ? this._context.lineTo(t, e) : (this._point = 1, this._context.moveTo(t, e));
  }
};
function pd(t) {
  return new Pa(t);
}
function ts(t) {
  return t < 0 ? -1 : 1;
}
function es(t, e, i) {
  var r = t._x1 - t._x0, n = e - t._x1, o = (t._y1 - t._y0) / (r || n < 0 && -0), s = (i - t._y1) / (n || r < 0 && -0), a = (o * n + s * r) / (r + n);
  return (ts(o) + ts(s)) * Math.min(Math.abs(o), Math.abs(s), 0.5 * Math.abs(a)) || 0;
}
function is(t, e) {
  var i = t._x1 - t._x0;
  return i ? (3 * (t._y1 - t._y0) / i - e) / 2 : e;
}
function Jr(t, e, i) {
  var r = t._x0, n = t._y0, o = t._x1, s = t._y1, a = (o - r) / 3;
  t._context.bezierCurveTo(r + a, n + a * e, o - a, s - a * i, o, s);
}
function or(t) {
  this._context = t;
}
or.prototype = {
  areaStart: function() {
    this._line = 0;
  },
  areaEnd: function() {
    this._line = NaN;
  },
  lineStart: function() {
    this._x0 = this._x1 = this._y0 = this._y1 = this._t0 = NaN, this._point = 0;
  },
  lineEnd: function() {
    switch (this._point) {
      case 2:
        this._context.lineTo(this._x1, this._y1);
        break;
      case 3:
        Jr(this, this._t0, is(this, this._t0));
        break;
    }
    (this._line || this._line !== 0 && this._point === 1) && this._context.closePath(), this._line = 1 - this._line;
  },
  point: function(t, e) {
    var i = NaN;
    if (t = +t, e = +e, !(t === this._x1 && e === this._y1)) {
      switch (this._point) {
        case 0:
          this._point = 1, this._line ? this._context.lineTo(t, e) : this._context.moveTo(t, e);
          break;
        case 1:
          this._point = 2;
          break;
        case 2:
          this._point = 3, Jr(this, is(this, i = es(this, t, e)), i);
          break;
        default:
          Jr(this, this._t0, i = es(this, t, e));
          break;
      }
      this._x0 = this._x1, this._x1 = t, this._y0 = this._y1, this._y1 = e, this._t0 = i;
    }
  }
};
function qa(t) {
  this._context = new za(t);
}
(qa.prototype = Object.create(or.prototype)).point = function(t, e) {
  or.prototype.point.call(this, e, t);
};
function za(t) {
  this._context = t;
}
za.prototype = {
  moveTo: function(t, e) {
    this._context.moveTo(e, t);
  },
  closePath: function() {
    this._context.closePath();
  },
  lineTo: function(t, e) {
    this._context.lineTo(e, t);
  },
  bezierCurveTo: function(t, e, i, r, n, o) {
    this._context.bezierCurveTo(e, t, r, i, o, n);
  }
};
function gd(t) {
  return new or(t);
}
function md(t) {
  return new qa(t);
}
function Wa(t) {
  this._context = t;
}
Wa.prototype = {
  areaStart: function() {
    this._line = 0;
  },
  areaEnd: function() {
    this._line = NaN;
  },
  lineStart: function() {
    this._x = [], this._y = [];
  },
  lineEnd: function() {
    var t = this._x, e = this._y, i = t.length;
    if (i)
      if (this._line ? this._context.lineTo(t[0], e[0]) : this._context.moveTo(t[0], e[0]), i === 2)
        this._context.lineTo(t[1], e[1]);
      else
        for (var r = rs(t), n = rs(e), o = 0, s = 1; s < i; ++o, ++s)
          this._context.bezierCurveTo(r[0][o], n[0][o], r[1][o], n[1][o], t[s], e[s]);
    (this._line || this._line !== 0 && i === 1) && this._context.closePath(), this._line = 1 - this._line, this._x = this._y = null;
  },
  point: function(t, e) {
    this._x.push(+t), this._y.push(+e);
  }
};
function rs(t) {
  var e, i = t.length - 1, r, n = new Array(i), o = new Array(i), s = new Array(i);
  for (n[0] = 0, o[0] = 2, s[0] = t[0] + 2 * t[1], e = 1; e < i - 1; ++e)
    n[e] = 1, o[e] = 4, s[e] = 4 * t[e] + 2 * t[e + 1];
  for (n[i - 1] = 2, o[i - 1] = 7, s[i - 1] = 8 * t[i - 1] + t[i], e = 1; e < i; ++e)
    r = n[e] / o[e - 1], o[e] -= r, s[e] -= r * s[e - 1];
  for (n[i - 1] = s[i - 1] / o[i - 1], e = i - 2; e >= 0; --e)
    n[e] = (s[e] - n[e + 1]) / o[e];
  for (o[i - 1] = (t[i] + n[i - 1]) / 2, e = 0; e < i - 1; ++e)
    o[e] = 2 * t[e + 1] - n[e + 1];
  return [n, o];
}
function yd(t) {
  return new Wa(t);
}
function Sr(t, e) {
  this._context = t, this._t = e;
}
Sr.prototype = {
  areaStart: function() {
    this._line = 0;
  },
  areaEnd: function() {
    this._line = NaN;
  },
  lineStart: function() {
    this._x = this._y = NaN, this._point = 0;
  },
  lineEnd: function() {
    0 < this._t && this._t < 1 && this._point === 2 && this._context.lineTo(this._x, this._y), (this._line || this._line !== 0 && this._point === 1) && this._context.closePath(), this._line >= 0 && (this._t = 1 - this._t, this._line = 1 - this._line);
  },
  point: function(t, e) {
    switch (t = +t, e = +e, this._point) {
      case 0:
        this._point = 1, this._line ? this._context.lineTo(t, e) : this._context.moveTo(t, e);
        break;
      case 1:
        this._point = 2;
      default: {
        if (this._t <= 0)
          this._context.lineTo(this._x, e), this._context.lineTo(t, e);
        else {
          var i = this._x * (1 - this._t) + t * this._t;
          this._context.lineTo(i, this._y), this._context.lineTo(i, e);
        }
        break;
      }
    }
    this._x = t, this._y = e;
  }
};
function _d(t) {
  return new Sr(t, 0.5);
}
function Cd(t) {
  return new Sr(t, 0);
}
function xd(t) {
  return new Sr(t, 1);
}
function oi(t, e, i) {
  this.k = t, this.x = e, this.y = i;
}
oi.prototype = {
  constructor: oi,
  scale: function(t) {
    return t === 1 ? this : new oi(this.k * t, this.x, this.y);
  },
  translate: function(t, e) {
    return t === 0 & e === 0 ? this : new oi(this.k, this.x + this.k * t, this.y + this.k * e);
  },
  apply: function(t) {
    return [t[0] * this.k + this.x, t[1] * this.k + this.y];
  },
  applyX: function(t) {
    return t * this.k + this.x;
  },
  applyY: function(t) {
    return t * this.k + this.y;
  },
  invert: function(t) {
    return [(t[0] - this.x) / this.k, (t[1] - this.y) / this.k];
  },
  invertX: function(t) {
    return (t - this.x) / this.k;
  },
  invertY: function(t) {
    return (t - this.y) / this.k;
  },
  rescaleX: function(t) {
    return t.copy().domain(t.range().map(this.invertX, this).map(t.invert, t));
  },
  rescaleY: function(t) {
    return t.copy().domain(t.range().map(this.invertY, this).map(t.invert, t));
  },
  toString: function() {
    return "translate(" + this.x + "," + this.y + ") scale(" + this.k + ")";
  }
};
oi.prototype;
/*! @license DOMPurify 3.4.2 | (c) Cure53 and other contributors | Released under the Apache license 2.0 and Mozilla Public License 2.0 | github.com/cure53/DOMPurify/blob/3.4.2/LICENSE */
const {
  entries: Ha,
  setPrototypeOf: ns,
  isFrozen: bd,
  getPrototypeOf: Td,
  getOwnPropertyDescriptor: Sd
} = Object;
let {
  freeze: pt,
  seal: Lt,
  create: Ee
} = Object, {
  apply: xn,
  construct: bn
} = typeof Reflect < "u" && Reflect;
pt || (pt = function(e) {
  return e;
});
Lt || (Lt = function(e) {
  return e;
});
xn || (xn = function(e, i) {
  for (var r = arguments.length, n = new Array(r > 2 ? r - 2 : 0), o = 2; o < r; o++)
    n[o - 2] = arguments[o];
  return e.apply(i, n);
});
bn || (bn = function(e) {
  for (var i = arguments.length, r = new Array(i > 1 ? i - 1 : 0), n = 1; n < i; n++)
    r[n - 1] = arguments[n];
  return new e(...r);
});
const Je = et(Array.prototype.forEach), kd = et(Array.prototype.lastIndexOf), os = et(Array.prototype.pop), Qe = et(Array.prototype.push), vd = et(Array.prototype.splice), ut = Array.isArray, si = et(String.prototype.toLowerCase), Qr = et(String.prototype.toString), ss = et(String.prototype.match), Fe = et(String.prototype.replace), as = et(String.prototype.indexOf), wd = et(String.prototype.trim), Bd = et(Number.prototype.toString), Ad = et(Boolean.prototype.toString), ls = typeof BigInt > "u" ? null : et(BigInt.prototype.toString), hs = typeof Symbol > "u" ? null : et(Symbol.prototype.toString), Z = et(Object.prototype.hasOwnProperty), ti = et(Object.prototype.toString), st = et(RegExp.prototype.test), $i = Fd(TypeError);
function et(t) {
  return function(e) {
    e instanceof RegExp && (e.lastIndex = 0);
    for (var i = arguments.length, r = new Array(i > 1 ? i - 1 : 0), n = 1; n < i; n++)
      r[n - 1] = arguments[n];
    return xn(t, e, r);
  };
}
function Fd(t) {
  return function() {
    for (var e = arguments.length, i = new Array(e), r = 0; r < e; r++)
      i[r] = arguments[r];
    return bn(t, i);
  };
}
function R(t, e) {
  let i = arguments.length > 2 && arguments[2] !== void 0 ? arguments[2] : si;
  if (ns && ns(t, null), !ut(e))
    return t;
  let r = e.length;
  for (; r--; ) {
    let n = e[r];
    if (typeof n == "string") {
      const o = i(n);
      o !== n && (bd(e) || (e[r] = o), n = o);
    }
    t[n] = !0;
  }
  return t;
}
function Ld(t) {
  for (let e = 0; e < t.length; e++)
    Z(t, e) || (t[e] = null);
  return t;
}
function yt(t) {
  const e = Ee(null);
  for (const [i, r] of Ha(t))
    Z(t, i) && (ut(r) ? e[i] = Ld(r) : r && typeof r == "object" && r.constructor === Object ? e[i] = yt(r) : e[i] = r);
  return e;
}
function Ed(t) {
  switch (typeof t) {
    case "string":
      return t;
    case "number":
      return Bd(t);
    case "boolean":
      return Ad(t);
    case "bigint":
      return ls ? ls(t) : "0";
    case "symbol":
      return hs ? hs(t) : "Symbol()";
    case "undefined":
      return ti(t);
    case "function":
    case "object": {
      if (t === null)
        return ti(t);
      const e = t, i = Oe(e, "toString");
      if (typeof i == "function") {
        const r = i(e);
        return typeof r == "string" ? r : ti(r);
      }
      return ti(t);
    }
    default:
      return ti(t);
  }
}
function Oe(t, e) {
  for (; t !== null; ) {
    const r = Sd(t, e);
    if (r) {
      if (r.get)
        return et(r.get);
      if (typeof r.value == "function")
        return et(r.value);
    }
    t = Td(t);
  }
  function i() {
    return null;
  }
  return i;
}
function Od(t) {
  try {
    return st(t, ""), !0;
  } catch {
    return !1;
  }
}
const cs = pt(["a", "abbr", "acronym", "address", "area", "article", "aside", "audio", "b", "bdi", "bdo", "big", "blink", "blockquote", "body", "br", "button", "canvas", "caption", "center", "cite", "code", "col", "colgroup", "content", "data", "datalist", "dd", "decorator", "del", "details", "dfn", "dialog", "dir", "div", "dl", "dt", "element", "em", "fieldset", "figcaption", "figure", "font", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "head", "header", "hgroup", "hr", "html", "i", "img", "input", "ins", "kbd", "label", "legend", "li", "main", "map", "mark", "marquee", "menu", "menuitem", "meter", "nav", "nobr", "ol", "optgroup", "option", "output", "p", "picture", "pre", "progress", "q", "rp", "rt", "ruby", "s", "samp", "search", "section", "select", "shadow", "slot", "small", "source", "spacer", "span", "strike", "strong", "style", "sub", "summary", "sup", "table", "tbody", "td", "template", "textarea", "tfoot", "th", "thead", "time", "tr", "track", "tt", "u", "ul", "var", "video", "wbr"]), tn = pt(["svg", "a", "altglyph", "altglyphdef", "altglyphitem", "animatecolor", "animatemotion", "animatetransform", "circle", "clippath", "defs", "desc", "ellipse", "enterkeyhint", "exportparts", "filter", "font", "g", "glyph", "glyphref", "hkern", "image", "inputmode", "line", "lineargradient", "marker", "mask", "metadata", "mpath", "part", "path", "pattern", "polygon", "polyline", "radialgradient", "rect", "stop", "style", "switch", "symbol", "text", "textpath", "title", "tref", "tspan", "view", "vkern"]), en = pt(["feBlend", "feColorMatrix", "feComponentTransfer", "feComposite", "feConvolveMatrix", "feDiffuseLighting", "feDisplacementMap", "feDistantLight", "feDropShadow", "feFlood", "feFuncA", "feFuncB", "feFuncG", "feFuncR", "feGaussianBlur", "feImage", "feMerge", "feMergeNode", "feMorphology", "feOffset", "fePointLight", "feSpecularLighting", "feSpotLight", "feTile", "feTurbulence"]), Md = pt(["animate", "color-profile", "cursor", "discard", "font-face", "font-face-format", "font-face-name", "font-face-src", "font-face-uri", "foreignobject", "hatch", "hatchpath", "mesh", "meshgradient", "meshpatch", "meshrow", "missing-glyph", "script", "set", "solidcolor", "unknown", "use"]), rn = pt(["math", "menclose", "merror", "mfenced", "mfrac", "mglyph", "mi", "mlabeledtr", "mmultiscripts", "mn", "mo", "mover", "mpadded", "mphantom", "mroot", "mrow", "ms", "mspace", "msqrt", "mstyle", "msub", "msup", "msubsup", "mtable", "mtd", "mtext", "mtr", "munder", "munderover", "mprescripts"]), Id = pt(["maction", "maligngroup", "malignmark", "mlongdiv", "mscarries", "mscarry", "msgroup", "mstack", "msline", "msrow", "semantics", "annotation", "annotation-xml", "mprescripts", "none"]), us = pt(["#text"]), fs = pt(["accept", "action", "align", "alt", "autocapitalize", "autocomplete", "autopictureinpicture", "autoplay", "background", "bgcolor", "border", "capture", "cellpadding", "cellspacing", "checked", "cite", "class", "clear", "color", "cols", "colspan", "controls", "controlslist", "coords", "crossorigin", "datetime", "decoding", "default", "dir", "disabled", "disablepictureinpicture", "disableremoteplayback", "download", "draggable", "enctype", "enterkeyhint", "exportparts", "face", "for", "headers", "height", "hidden", "high", "href", "hreflang", "id", "inert", "inputmode", "integrity", "ismap", "kind", "label", "lang", "list", "loading", "loop", "low", "max", "maxlength", "media", "method", "min", "minlength", "multiple", "muted", "name", "nonce", "noshade", "novalidate", "nowrap", "open", "optimum", "part", "pattern", "placeholder", "playsinline", "popover", "popovertarget", "popovertargetaction", "poster", "preload", "pubdate", "radiogroup", "readonly", "rel", "required", "rev", "reversed", "role", "rows", "rowspan", "spellcheck", "scope", "selected", "shape", "size", "sizes", "slot", "span", "srclang", "start", "src", "srcset", "step", "style", "summary", "tabindex", "title", "translate", "type", "usemap", "valign", "value", "width", "wrap", "xmlns"]), nn = pt(["accent-height", "accumulate", "additive", "alignment-baseline", "amplitude", "ascent", "attributename", "attributetype", "azimuth", "basefrequency", "baseline-shift", "begin", "bias", "by", "class", "clip", "clippathunits", "clip-path", "clip-rule", "color", "color-interpolation", "color-interpolation-filters", "color-profile", "color-rendering", "cx", "cy", "d", "dx", "dy", "diffuseconstant", "direction", "display", "divisor", "dur", "edgemode", "elevation", "end", "exponent", "fill", "fill-opacity", "fill-rule", "filter", "filterunits", "flood-color", "flood-opacity", "font-family", "font-size", "font-size-adjust", "font-stretch", "font-style", "font-variant", "font-weight", "fx", "fy", "g1", "g2", "glyph-name", "glyphref", "gradientunits", "gradienttransform", "height", "href", "id", "image-rendering", "in", "in2", "intercept", "k", "k1", "k2", "k3", "k4", "kerning", "keypoints", "keysplines", "keytimes", "lang", "lengthadjust", "letter-spacing", "kernelmatrix", "kernelunitlength", "lighting-color", "local", "marker-end", "marker-mid", "marker-start", "markerheight", "markerunits", "markerwidth", "maskcontentunits", "maskunits", "max", "mask", "mask-type", "media", "method", "mode", "min", "name", "numoctaves", "offset", "operator", "opacity", "order", "orient", "orientation", "origin", "overflow", "paint-order", "path", "pathlength", "patterncontentunits", "patterntransform", "patternunits", "points", "preservealpha", "preserveaspectratio", "primitiveunits", "r", "rx", "ry", "radius", "refx", "refy", "repeatcount", "repeatdur", "restart", "result", "rotate", "scale", "seed", "shape-rendering", "slope", "specularconstant", "specularexponent", "spreadmethod", "startoffset", "stddeviation", "stitchtiles", "stop-color", "stop-opacity", "stroke-dasharray", "stroke-dashoffset", "stroke-linecap", "stroke-linejoin", "stroke-miterlimit", "stroke-opacity", "stroke", "stroke-width", "style", "surfacescale", "systemlanguage", "tabindex", "tablevalues", "targetx", "targety", "transform", "transform-origin", "text-anchor", "text-decoration", "text-rendering", "textlength", "type", "u1", "u2", "unicode", "values", "viewbox", "visibility", "version", "vert-adv-y", "vert-origin-x", "vert-origin-y", "width", "word-spacing", "wrap", "writing-mode", "xchannelselector", "ychannelselector", "x", "x1", "x2", "xmlns", "y", "y1", "y2", "z", "zoomandpan"]), ds = pt(["accent", "accentunder", "align", "bevelled", "close", "columnalign", "columnlines", "columnspacing", "columnspan", "denomalign", "depth", "dir", "display", "displaystyle", "encoding", "fence", "frame", "height", "href", "id", "largeop", "length", "linethickness", "lquote", "lspace", "mathbackground", "mathcolor", "mathsize", "mathvariant", "maxsize", "minsize", "movablelimits", "notation", "numalign", "open", "rowalign", "rowlines", "rowspacing", "rowspan", "rspace", "rquote", "scriptlevel", "scriptminsize", "scriptsizemultiplier", "selection", "separator", "separators", "stretchy", "subscriptshift", "supscriptshift", "symmetric", "voffset", "width", "xmlns"]), Di = pt(["xlink:href", "xml:id", "xlink:title", "xml:space", "xmlns:xlink"]), $d = Lt(/\{\{[\w\W]*|[\w\W]*\}\}/gm), Dd = Lt(/<%[\w\W]*|[\w\W]*%>/gm), Nd = Lt(/\$\{[\w\W]*/gm), Rd = Lt(/^data-[\-\w.\u00B7-\uFFFF]+$/), Pd = Lt(/^aria-[\-\w]+$/), ja = Lt(
  /^(?:(?:(?:f|ht)tps?|mailto|tel|callto|sms|cid|xmpp|matrix):|[^a-z]|[a-z+.\-]+(?:[^a-z+.\-:]|$))/i
  // eslint-disable-line no-useless-escape
), qd = Lt(/^(?:\w+script|data):/i), zd = Lt(
  /[\u0000-\u0020\u00A0\u1680\u180E\u2000-\u2029\u205F\u3000]/g
  // eslint-disable-line no-control-regex
), Ua = Lt(/^html$/i), Wd = Lt(/^[a-z][.\w]*(-[.\w]+)+$/i);
var ps = /* @__PURE__ */ Object.freeze({
  __proto__: null,
  ARIA_ATTR: Pd,
  ATTR_WHITESPACE: zd,
  CUSTOM_ELEMENT: Wd,
  DATA_ATTR: Rd,
  DOCTYPE_NAME: Ua,
  ERB_EXPR: Dd,
  IS_ALLOWED_URI: ja,
  IS_SCRIPT_OR_DATA: qd,
  MUSTACHE_EXPR: $d,
  TMPLIT_EXPR: Nd
});
const ei = {
  element: 1,
  text: 3,
  // Deprecated
  progressingInstruction: 7,
  comment: 8,
  document: 9
}, Hd = function() {
  return typeof window > "u" ? null : window;
}, jd = function(e, i) {
  if (typeof e != "object" || typeof e.createPolicy != "function")
    return null;
  let r = null;
  const n = "data-tt-policy-suffix";
  i && i.hasAttribute(n) && (r = i.getAttribute(n));
  const o = "dompurify" + (r ? "#" + r : "");
  try {
    return e.createPolicy(o, {
      createHTML(s) {
        return s;
      },
      createScriptURL(s) {
        return s;
      }
    });
  } catch {
    return console.warn("TrustedTypes policy " + o + " could not be created."), null;
  }
}, gs = function() {
  return {
    afterSanitizeAttributes: [],
    afterSanitizeElements: [],
    afterSanitizeShadowDOM: [],
    beforeSanitizeAttributes: [],
    beforeSanitizeElements: [],
    beforeSanitizeShadowDOM: [],
    uponSanitizeAttribute: [],
    uponSanitizeElement: [],
    uponSanitizeShadowNode: []
  };
};
function Ya() {
  let t = arguments.length > 0 && arguments[0] !== void 0 ? arguments[0] : Hd();
  const e = (E) => Ya(E);
  if (e.version = "3.4.2", e.removed = [], !t || !t.document || t.document.nodeType !== ei.document || !t.Element)
    return e.isSupported = !1, e;
  let {
    document: i
  } = t;
  const r = i, n = r.currentScript, {
    DocumentFragment: o,
    HTMLTemplateElement: s,
    Node: a,
    Element: l,
    NodeFilter: h,
    NamedNodeMap: u = t.NamedNodeMap || t.MozNamedAttrMap,
    HTMLFormElement: d,
    DOMParser: c,
    trustedTypes: p
  } = t, y = l.prototype, S = Oe(y, "cloneNode"), M = Oe(y, "remove"), q = Oe(y, "nextSibling"), T = Oe(y, "childNodes"), U = Oe(y, "parentNode");
  if (typeof s == "function") {
    const E = i.createElement("template");
    E.content && E.content.ownerDocument && (i = E.content.ownerDocument);
  }
  let W, G = "";
  const {
    implementation: V,
    createNodeIterator: H,
    createDocumentFragment: ue,
    getElementsByTagName: re
  } = i, {
    importNode: j
  } = r;
  let $ = gs();
  e.isSupported = typeof Ha == "function" && typeof U == "function" && V && V.createHTMLDocument !== void 0;
  const {
    MUSTACHE_EXPR: bt,
    ERB_EXPR: Rt,
    TMPLIT_EXPR: L,
    DATA_ATTR: b,
    ARIA_ATTR: C,
    IS_SCRIPT_OR_DATA: v,
    ATTR_WHITESPACE: x,
    CUSTOM_ELEMENT: F
  } = ps;
  let {
    IS_ALLOWED_URI: N
  } = ps, D = null;
  const K = R({}, [...cs, ...tn, ...en, ...rn, ...us]);
  let P = null;
  const it = R({}, [...fs, ...nn, ...ds, ...Di]);
  let z = Object.seal(Ee(null, {
    tagNameCheck: {
      writable: !0,
      configurable: !1,
      enumerable: !0,
      value: null
    },
    attributeNameCheck: {
      writable: !0,
      configurable: !1,
      enumerable: !0,
      value: null
    },
    allowCustomizedBuiltInElements: {
      writable: !0,
      configurable: !1,
      enumerable: !0,
      value: !1
    }
  })), St = null, kt = null;
  const gt = Object.seal(Ee(null, {
    tagCheck: {
      writable: !0,
      configurable: !1,
      enumerable: !0,
      value: null
    },
    attributeCheck: {
      writable: !0,
      configurable: !1,
      enumerable: !0,
      value: null
    }
  }));
  let Pt = !0, nt = !0, Ot = !1, ne = !0, vt = !1, Ve = !0, fe = !1, Nr = !1, Rr = !1, ve = !1, Ai = !1, Fi = !1, _o = !0, Co = !1;
  const xo = "user-content-";
  let Pr = !0, Xe = !1, we = {}, qt = null;
  const qr = R({}, ["annotation-xml", "audio", "colgroup", "desc", "foreignobject", "head", "iframe", "math", "mi", "mn", "mo", "ms", "mtext", "noembed", "noframes", "noscript", "plaintext", "script", "style", "svg", "template", "thead", "title", "video", "xmp"]);
  let bo = null;
  const To = R({}, ["audio", "video", "img", "source", "image", "track"]);
  let zr = null;
  const So = R({}, ["alt", "class", "for", "id", "label", "name", "pattern", "placeholder", "role", "summary", "title", "value", "style", "xmlns"]), Li = "http://www.w3.org/1998/Math/MathML", Ei = "http://www.w3.org/2000/svg", zt = "http://www.w3.org/1999/xhtml";
  let Be = zt, Wr = !1, Hr = null;
  const Ah = R({}, [Li, Ei, zt], Qr);
  let jr = R({}, ["mi", "mo", "mn", "ms", "mtext"]), Ur = R({}, ["annotation-xml"]);
  const Fh = R({}, ["title", "style", "font", "a", "script"]);
  let Ke = null;
  const Lh = ["application/xhtml+xml", "text/html"], Eh = "text/html";
  let rt = null, Ae = null;
  const Oh = i.createElement("form"), ko = function(f) {
    return f instanceof RegExp || f instanceof Function;
  }, Yr = function() {
    let f = arguments.length > 0 && arguments[0] !== void 0 ? arguments[0] : {};
    if (Ae && Ae === f)
      return;
    (!f || typeof f != "object") && (f = {}), f = yt(f), Ke = // eslint-disable-next-line unicorn/prefer-includes
    Lh.indexOf(f.PARSER_MEDIA_TYPE) === -1 ? Eh : f.PARSER_MEDIA_TYPE, rt = Ke === "application/xhtml+xml" ? Qr : si, D = Z(f, "ALLOWED_TAGS") && ut(f.ALLOWED_TAGS) ? R({}, f.ALLOWED_TAGS, rt) : K, P = Z(f, "ALLOWED_ATTR") && ut(f.ALLOWED_ATTR) ? R({}, f.ALLOWED_ATTR, rt) : it, Hr = Z(f, "ALLOWED_NAMESPACES") && ut(f.ALLOWED_NAMESPACES) ? R({}, f.ALLOWED_NAMESPACES, Qr) : Ah, zr = Z(f, "ADD_URI_SAFE_ATTR") && ut(f.ADD_URI_SAFE_ATTR) ? R(yt(So), f.ADD_URI_SAFE_ATTR, rt) : So, bo = Z(f, "ADD_DATA_URI_TAGS") && ut(f.ADD_DATA_URI_TAGS) ? R(yt(To), f.ADD_DATA_URI_TAGS, rt) : To, qt = Z(f, "FORBID_CONTENTS") && ut(f.FORBID_CONTENTS) ? R({}, f.FORBID_CONTENTS, rt) : qr, St = Z(f, "FORBID_TAGS") && ut(f.FORBID_TAGS) ? R({}, f.FORBID_TAGS, rt) : yt({}), kt = Z(f, "FORBID_ATTR") && ut(f.FORBID_ATTR) ? R({}, f.FORBID_ATTR, rt) : yt({}), we = Z(f, "USE_PROFILES") ? f.USE_PROFILES && typeof f.USE_PROFILES == "object" ? yt(f.USE_PROFILES) : f.USE_PROFILES : !1, Pt = f.ALLOW_ARIA_ATTR !== !1, nt = f.ALLOW_DATA_ATTR !== !1, Ot = f.ALLOW_UNKNOWN_PROTOCOLS || !1, ne = f.ALLOW_SELF_CLOSE_IN_ATTR !== !1, vt = f.SAFE_FOR_TEMPLATES || !1, Ve = f.SAFE_FOR_XML !== !1, fe = f.WHOLE_DOCUMENT || !1, ve = f.RETURN_DOM || !1, Ai = f.RETURN_DOM_FRAGMENT || !1, Fi = f.RETURN_TRUSTED_TYPE || !1, Rr = f.FORCE_BODY || !1, _o = f.SANITIZE_DOM !== !1, Co = f.SANITIZE_NAMED_PROPS || !1, Pr = f.KEEP_CONTENT !== !1, Xe = f.IN_PLACE || !1, N = Od(f.ALLOWED_URI_REGEXP) ? f.ALLOWED_URI_REGEXP : ja, Be = typeof f.NAMESPACE == "string" ? f.NAMESPACE : zt, jr = Z(f, "MATHML_TEXT_INTEGRATION_POINTS") && f.MATHML_TEXT_INTEGRATION_POINTS && typeof f.MATHML_TEXT_INTEGRATION_POINTS == "object" ? yt(f.MATHML_TEXT_INTEGRATION_POINTS) : R({}, ["mi", "mo", "mn", "ms", "mtext"]), Ur = Z(f, "HTML_INTEGRATION_POINTS") && f.HTML_INTEGRATION_POINTS && typeof f.HTML_INTEGRATION_POINTS == "object" ? yt(f.HTML_INTEGRATION_POINTS) : R({}, ["annotation-xml"]);
    const m = Z(f, "CUSTOM_ELEMENT_HANDLING") && f.CUSTOM_ELEMENT_HANDLING && typeof f.CUSTOM_ELEMENT_HANDLING == "object" ? yt(f.CUSTOM_ELEMENT_HANDLING) : Ee(null);
    if (z = Ee(null), Z(m, "tagNameCheck") && ko(m.tagNameCheck) && (z.tagNameCheck = m.tagNameCheck), Z(m, "attributeNameCheck") && ko(m.attributeNameCheck) && (z.attributeNameCheck = m.attributeNameCheck), Z(m, "allowCustomizedBuiltInElements") && typeof m.allowCustomizedBuiltInElements == "boolean" && (z.allowCustomizedBuiltInElements = m.allowCustomizedBuiltInElements), vt && (nt = !1), Ai && (ve = !0), we && (D = R({}, us), P = Ee(null), we.html === !0 && (R(D, cs), R(P, fs)), we.svg === !0 && (R(D, tn), R(P, nn), R(P, Di)), we.svgFilters === !0 && (R(D, en), R(P, nn), R(P, Di)), we.mathMl === !0 && (R(D, rn), R(P, ds), R(P, Di))), gt.tagCheck = null, gt.attributeCheck = null, Z(f, "ADD_TAGS") && (typeof f.ADD_TAGS == "function" ? gt.tagCheck = f.ADD_TAGS : ut(f.ADD_TAGS) && (D === K && (D = yt(D)), R(D, f.ADD_TAGS, rt))), Z(f, "ADD_ATTR") && (typeof f.ADD_ATTR == "function" ? gt.attributeCheck = f.ADD_ATTR : ut(f.ADD_ATTR) && (P === it && (P = yt(P)), R(P, f.ADD_ATTR, rt))), Z(f, "ADD_URI_SAFE_ATTR") && ut(f.ADD_URI_SAFE_ATTR) && R(zr, f.ADD_URI_SAFE_ATTR, rt), Z(f, "FORBID_CONTENTS") && ut(f.FORBID_CONTENTS) && (qt === qr && (qt = yt(qt)), R(qt, f.FORBID_CONTENTS, rt)), Z(f, "ADD_FORBID_CONTENTS") && ut(f.ADD_FORBID_CONTENTS) && (qt === qr && (qt = yt(qt)), R(qt, f.ADD_FORBID_CONTENTS, rt)), Pr && (D["#text"] = !0), fe && R(D, ["html", "head", "body"]), D.table && (R(D, ["tbody"]), delete St.tbody), f.TRUSTED_TYPES_POLICY) {
      if (typeof f.TRUSTED_TYPES_POLICY.createHTML != "function")
        throw $i('TRUSTED_TYPES_POLICY configuration option must provide a "createHTML" hook.');
      if (typeof f.TRUSTED_TYPES_POLICY.createScriptURL != "function")
        throw $i('TRUSTED_TYPES_POLICY configuration option must provide a "createScriptURL" hook.');
      W = f.TRUSTED_TYPES_POLICY, G = W.createHTML("");
    } else
      W === void 0 && (W = jd(p, n)), W !== null && typeof G == "string" && (G = W.createHTML(""));
    pt && pt(f), Ae = f;
  }, vo = R({}, [...tn, ...en, ...Md]), wo = R({}, [...rn, ...Id]), Mh = function(f) {
    let m = U(f);
    (!m || !m.tagName) && (m = {
      namespaceURI: Be,
      tagName: "template"
    });
    const k = si(f.tagName), Y = si(m.tagName);
    return Hr[f.namespaceURI] ? f.namespaceURI === Ei ? m.namespaceURI === zt ? k === "svg" : m.namespaceURI === Li ? k === "svg" && (Y === "annotation-xml" || jr[Y]) : !!vo[k] : f.namespaceURI === Li ? m.namespaceURI === zt ? k === "math" : m.namespaceURI === Ei ? k === "math" && Ur[Y] : !!wo[k] : f.namespaceURI === zt ? m.namespaceURI === Ei && !Ur[Y] || m.namespaceURI === Li && !jr[Y] ? !1 : !wo[k] && (Fh[k] || !vo[k]) : !!(Ke === "application/xhtml+xml" && Hr[f.namespaceURI]) : !1;
  }, Mt = function(f) {
    Qe(e.removed, {
      element: f
    });
    try {
      U(f).removeChild(f);
    } catch {
      M(f);
    }
  }, de = function(f, m) {
    try {
      Qe(e.removed, {
        attribute: m.getAttributeNode(f),
        from: m
      });
    } catch {
      Qe(e.removed, {
        attribute: null,
        from: m
      });
    }
    if (m.removeAttribute(f), f === "is")
      if (ve || Ai)
        try {
          Mt(m);
        } catch {
        }
      else
        try {
          m.setAttribute(f, "");
        } catch {
        }
  }, Bo = function(f) {
    let m = null, k = null;
    if (Rr)
      f = "<remove></remove>" + f;
    else {
      const J = ss(f, /^[\r\n\t ]+/);
      k = J && J[0];
    }
    Ke === "application/xhtml+xml" && Be === zt && (f = '<html xmlns="http://www.w3.org/1999/xhtml"><head></head><body>' + f + "</body></html>");
    const Y = W ? W.createHTML(f) : f;
    if (Be === zt)
      try {
        m = new c().parseFromString(Y, Ke);
      } catch {
      }
    if (!m || !m.documentElement) {
      m = V.createDocument(Be, "template", null);
      try {
        m.documentElement.innerHTML = Wr ? G : Y;
      } catch {
      }
    }
    const at = m.body || m.documentElement;
    return f && k && at.insertBefore(i.createTextNode(k), at.childNodes[0] || null), Be === zt ? re.call(m, fe ? "html" : "body")[0] : fe ? m.documentElement : at;
  }, Ao = function(f) {
    return H.call(
      f.ownerDocument || f,
      f,
      // eslint-disable-next-line no-bitwise
      h.SHOW_ELEMENT | h.SHOW_COMMENT | h.SHOW_TEXT | h.SHOW_PROCESSING_INSTRUCTION | h.SHOW_CDATA_SECTION,
      null
    );
  }, Gr = function(f) {
    return f instanceof d && (typeof f.nodeName != "string" || typeof f.textContent != "string" || typeof f.removeChild != "function" || !(f.attributes instanceof u) || typeof f.removeAttribute != "function" || typeof f.setAttribute != "function" || typeof f.namespaceURI != "string" || typeof f.insertBefore != "function" || typeof f.hasChildNodes != "function");
  }, Vr = function(f) {
    return typeof a == "function" && f instanceof a;
  };
  function Vt(E, f, m) {
    Je(E, (k) => {
      k.call(e, f, m, Ae);
    });
  }
  const Fo = function(f) {
    let m = null;
    if (Vt($.beforeSanitizeElements, f, null), Gr(f))
      return Mt(f), !0;
    const k = rt(f.nodeName);
    if (Vt($.uponSanitizeElement, f, {
      tagName: k,
      allowedTags: D
    }), Ve && f.hasChildNodes() && !Vr(f.firstElementChild) && st(/<[/\w!]/g, f.innerHTML) && st(/<[/\w!]/g, f.textContent) || Ve && f.namespaceURI === zt && k === "style" && Vr(f.firstElementChild) || f.nodeType === ei.progressingInstruction || Ve && f.nodeType === ei.comment && st(/<[/\w]/g, f.data))
      return Mt(f), !0;
    if (St[k] || !(gt.tagCheck instanceof Function && gt.tagCheck(k)) && !D[k]) {
      if (!St[k] && Eo(k) && (z.tagNameCheck instanceof RegExp && st(z.tagNameCheck, k) || z.tagNameCheck instanceof Function && z.tagNameCheck(k)))
        return !1;
      if (Pr && !qt[k]) {
        const Y = U(f) || f.parentNode, at = T(f) || f.childNodes;
        if (at && Y) {
          const J = at.length;
          for (let mt = J - 1; mt >= 0; --mt) {
            const wt = S(at[mt], !0);
            Y.insertBefore(wt, q(f));
          }
        }
      }
      return Mt(f), !0;
    }
    return f instanceof l && !Mh(f) || (k === "noscript" || k === "noembed" || k === "noframes") && st(/<\/no(script|embed|frames)/i, f.innerHTML) ? (Mt(f), !0) : (vt && f.nodeType === ei.text && (m = f.textContent, Je([bt, Rt, L], (Y) => {
      m = Fe(m, Y, " ");
    }), f.textContent !== m && (Qe(e.removed, {
      element: f.cloneNode()
    }), f.textContent = m)), Vt($.afterSanitizeElements, f, null), !1);
  }, Lo = function(f, m, k) {
    if (kt[m] || _o && (m === "id" || m === "name") && (k in i || k in Oh))
      return !1;
    const Y = P[m] || gt.attributeCheck instanceof Function && gt.attributeCheck(m, f);
    if (!(nt && !kt[m] && st(b, m))) {
      if (!(Pt && st(C, m))) {
        if (!Y || kt[m]) {
          if (
            // First condition does a very basic check if a) it's basically a valid custom element tagname AND
            // b) if the tagName passes whatever the user has configured for CUSTOM_ELEMENT_HANDLING.tagNameCheck
            // and c) if the attribute name passes whatever the user has configured for CUSTOM_ELEMENT_HANDLING.attributeNameCheck
            !(Eo(f) && (z.tagNameCheck instanceof RegExp && st(z.tagNameCheck, f) || z.tagNameCheck instanceof Function && z.tagNameCheck(f)) && (z.attributeNameCheck instanceof RegExp && st(z.attributeNameCheck, m) || z.attributeNameCheck instanceof Function && z.attributeNameCheck(m, f)) || // Alternative, second condition checks if it's an `is`-attribute, AND
            // the value passes whatever the user has configured for CUSTOM_ELEMENT_HANDLING.tagNameCheck
            m === "is" && z.allowCustomizedBuiltInElements && (z.tagNameCheck instanceof RegExp && st(z.tagNameCheck, k) || z.tagNameCheck instanceof Function && z.tagNameCheck(k)))
          )
            return !1;
        } else if (!zr[m]) {
          if (!st(N, Fe(k, x, ""))) {
            if (!((m === "src" || m === "xlink:href" || m === "href") && f !== "script" && as(k, "data:") === 0 && bo[f])) {
              if (!(Ot && !st(v, Fe(k, x, "")))) {
                if (k)
                  return !1;
              }
            }
          }
        }
      }
    }
    return !0;
  }, Ih = R({}, ["annotation-xml", "color-profile", "font-face", "font-face-format", "font-face-name", "font-face-src", "font-face-uri", "missing-glyph"]), Eo = function(f) {
    return !Ih[si(f)] && st(F, f);
  }, Oo = function(f) {
    Vt($.beforeSanitizeAttributes, f, null);
    const {
      attributes: m
    } = f;
    if (!m || Gr(f))
      return;
    const k = {
      attrName: "",
      attrValue: "",
      keepAttr: !0,
      allowedAttributes: P,
      forceKeepAttr: void 0
    };
    let Y = m.length;
    for (; Y--; ) {
      const at = m[Y], {
        name: J,
        namespaceURI: mt,
        value: wt
      } = at, It = rt(J), Xr = wt;
      let ot = J === "value" ? Xr : wd(Xr);
      if (k.attrName = It, k.attrValue = ot, k.keepAttr = !0, k.forceKeepAttr = void 0, Vt($.uponSanitizeAttribute, f, k), ot = k.attrValue, Co && (It === "id" || It === "name") && as(ot, xo) !== 0 && (de(J, f), ot = xo + ot), Ve && st(/((--!?|])>)|<\/(style|script|title|xmp|textarea|noscript|iframe|noembed|noframes)/i, ot)) {
        de(J, f);
        continue;
      }
      if (It === "attributename" && ss(ot, "href")) {
        de(J, f);
        continue;
      }
      if (k.forceKeepAttr)
        continue;
      if (!k.keepAttr) {
        de(J, f);
        continue;
      }
      if (!ne && st(/\/>/i, ot)) {
        de(J, f);
        continue;
      }
      vt && Je([bt, Rt, L], ($o) => {
        ot = Fe(ot, $o, " ");
      });
      const Io = rt(f.nodeName);
      if (!Lo(Io, It, ot)) {
        de(J, f);
        continue;
      }
      if (W && typeof p == "object" && typeof p.getAttributeType == "function" && !mt)
        switch (p.getAttributeType(Io, It)) {
          case "TrustedHTML": {
            ot = W.createHTML(ot);
            break;
          }
          case "TrustedScriptURL": {
            ot = W.createScriptURL(ot);
            break;
          }
        }
      if (ot !== Xr)
        try {
          mt ? f.setAttributeNS(mt, J, ot) : f.setAttribute(J, ot), Gr(f) ? Mt(f) : os(e.removed);
        } catch {
          de(J, f);
        }
    }
    Vt($.afterSanitizeAttributes, f, null);
  }, Mo = function(f) {
    let m = null;
    const k = Ao(f);
    for (Vt($.beforeSanitizeShadowDOM, f, null); m = k.nextNode(); )
      Vt($.uponSanitizeShadowNode, m, null), Fo(m), Oo(m), m.content instanceof o && Mo(m.content);
    Vt($.afterSanitizeShadowDOM, f, null);
  };
  return e.sanitize = function(E) {
    let f = arguments.length > 1 && arguments[1] !== void 0 ? arguments[1] : {}, m = null, k = null, Y = null, at = null;
    if (Wr = !E, Wr && (E = "<!-->"), typeof E != "string" && !Vr(E) && (E = Ed(E), typeof E != "string"))
      throw $i("dirty is not a string, aborting");
    if (!e.isSupported)
      return E;
    if (Nr || Yr(f), e.removed = [], typeof E == "string" && (Xe = !1), Xe) {
      const wt = E.nodeName;
      if (typeof wt == "string") {
        const It = rt(wt);
        if (!D[It] || St[It])
          throw $i("root node is forbidden and cannot be sanitized in-place");
      }
    } else if (E instanceof a)
      m = Bo("<!---->"), k = m.ownerDocument.importNode(E, !0), k.nodeType === ei.element && k.nodeName === "BODY" || k.nodeName === "HTML" ? m = k : m.appendChild(k);
    else {
      if (!ve && !vt && !fe && // eslint-disable-next-line unicorn/prefer-includes
      E.indexOf("<") === -1)
        return W && Fi ? W.createHTML(E) : E;
      if (m = Bo(E), !m)
        return ve ? null : Fi ? G : "";
    }
    m && Rr && Mt(m.firstChild);
    const J = Ao(Xe ? E : m);
    for (; Y = J.nextNode(); )
      Fo(Y), Oo(Y), Y.content instanceof o && Mo(Y.content);
    if (Xe)
      return E;
    if (ve) {
      if (vt) {
        m.normalize();
        let wt = m.innerHTML;
        Je([bt, Rt, L], (It) => {
          wt = Fe(wt, It, " ");
        }), m.innerHTML = wt;
      }
      if (Ai)
        for (at = ue.call(m.ownerDocument); m.firstChild; )
          at.appendChild(m.firstChild);
      else
        at = m;
      return (P.shadowroot || P.shadowrootmode) && (at = j.call(r, at, !0)), at;
    }
    let mt = fe ? m.outerHTML : m.innerHTML;
    return fe && D["!doctype"] && m.ownerDocument && m.ownerDocument.doctype && m.ownerDocument.doctype.name && st(Ua, m.ownerDocument.doctype.name) && (mt = "<!DOCTYPE " + m.ownerDocument.doctype.name + `>
` + mt), vt && Je([bt, Rt, L], (wt) => {
      mt = Fe(mt, wt, " ");
    }), W && Fi ? W.createHTML(mt) : mt;
  }, e.setConfig = function() {
    let E = arguments.length > 0 && arguments[0] !== void 0 ? arguments[0] : {};
    Yr(E), Nr = !0;
  }, e.clearConfig = function() {
    Ae = null, Nr = !1;
  }, e.isValidAttribute = function(E, f, m) {
    Ae || Yr({});
    const k = rt(E), Y = rt(f);
    return Lo(k, Y, m);
  }, e.addHook = function(E, f) {
    typeof f == "function" && Qe($[E], f);
  }, e.removeHook = function(E, f) {
    if (f !== void 0) {
      const m = kd($[E], f);
      return m === -1 ? void 0 : vd($[E], m, 1)[0];
    }
    return os($[E]);
  }, e.removeHooks = function(E) {
    $[E] = [];
  }, e.removeAllHooks = function() {
    $ = gs();
  }, e;
}
var Re = Ya();
const ki = /<br\s*\/?>/gi, Ud = (t) => t ? Va(t).replace(/\\n/g, "#br#").split("#br#") : [""], Yd = (() => {
  let t = !1;
  return () => {
    t || (Gd(), t = !0);
  };
})();
function Gd() {
  const t = "data-temp-href-target";
  Re.addHook("beforeSanitizeAttributes", (e) => {
    e.tagName === "A" && e.hasAttribute("target") && e.setAttribute(t, e.getAttribute("target") ?? "");
  }), Re.addHook("afterSanitizeAttributes", (e) => {
    e.tagName === "A" && e.hasAttribute(t) && (e.setAttribute("target", e.getAttribute(t) ?? ""), e.removeAttribute(t), e.getAttribute("target") === "_blank" && e.setAttribute("rel", "noopener"));
  });
}
const Ga = (t) => (Yd(), Re.sanitize(t)), ms = (t, e) => {
  var i;
  if (((i = e.flowchart) == null ? void 0 : i.htmlLabels) !== !1) {
    const r = e.securityLevel;
    r === "antiscript" || r === "strict" ? t = Ga(t) : r !== "loose" && (t = Va(t), t = t.replace(/</g, "&lt;").replace(/>/g, "&gt;"), t = t.replace(/=/g, "&equals;"), t = Zd(t));
  }
  return t;
}, Pe = (t, e) => t && (e.dompurifyConfig ? t = Re.sanitize(ms(t, e), e.dompurifyConfig).toString() : t = Re.sanitize(ms(t, e), {
  FORBID_TAGS: ["style"]
}).toString(), t), Vd = (t, e) => typeof t == "string" ? Pe(t, e) : t.flat().map((i) => Pe(i, e)), Xd = (t) => ki.test(t), Kd = (t) => t.split(ki), Zd = (t) => t.replace(/#br#/g, "<br/>"), Va = (t) => t.replace(ki, "#br#"), Jd = (t) => {
  let e = "";
  return t && (e = window.location.protocol + "//" + window.location.host + window.location.pathname + window.location.search, e = e.replaceAll(/\(/g, "\\("), e = e.replaceAll(/\)/g, "\\)")), e;
}, Xa = (t) => !(t === !1 || ["false", "null", "0"].includes(String(t).trim().toLowerCase())), Qd = function(...t) {
  const e = t.filter((i) => !isNaN(i));
  return Math.max(...e);
}, tp = function(...t) {
  const e = t.filter((i) => !isNaN(i));
  return Math.min(...e);
}, j1 = function(t) {
  const e = t.split(/(,)/), i = [];
  for (let r = 0; r < e.length; r++) {
    let n = e[r];
    if (n === "," && r > 0 && r + 1 < e.length) {
      const o = e[r - 1], s = e[r + 1];
      ep(o, s) && (n = o + "," + s, r++, i.pop());
    }
    i.push(ip(n));
  }
  return i.join("");
}, Tn = (t, e) => Math.max(0, t.split(e).length - 1), ep = (t, e) => {
  const i = Tn(t, "~"), r = Tn(e, "~");
  return i === 1 && r === 1;
}, ip = (t) => {
  const e = Tn(t, "~");
  let i = !1;
  if (e <= 1)
    return t;
  e % 2 !== 0 && t.startsWith("~") && (t = t.substring(1), i = !0);
  const r = [...t];
  let n = r.indexOf("~"), o = r.lastIndexOf("~");
  for (; n !== -1 && o !== -1 && n !== o; )
    r[n] = "<", r[o] = ">", n = r.indexOf("~"), o = r.lastIndexOf("~");
  return i && r.unshift("~"), r.join("");
}, ys = () => window.MathMLElement !== void 0, Sn = /\$\$(.*)\$\$/g, _s = (t) => {
  var e;
  return (((e = t.match(Sn)) == null ? void 0 : e.length) ?? 0) > 0;
}, U1 = async (t, e) => {
  const i = document.createElement("div");
  i.innerHTML = await np(t, e), i.id = "katex-temp", i.style.visibility = "hidden", i.style.position = "absolute", i.style.top = "0";
  const r = document.querySelector("body");
  r == null || r.insertAdjacentElement("beforeend", i);
  const n = { width: i.clientWidth, height: i.clientHeight };
  return i.remove(), n;
}, rp = async (t, e) => {
  if (!_s(t))
    return t;
  if (!ys() && !e.legacyMathML)
    return t.replace(Sn, "MathML is unsupported in this environment.");
  const { default: i } = await import("./katex-7aea786e.js");
  return t.split(ki).map(
    (r) => _s(r) ? `
            <div style="display: flex; align-items: center; justify-content: center; white-space: nowrap;">
              ${r}
            </div>
          ` : `<div>${r}</div>`
  ).join("").replace(
    Sn,
    (r, n) => i.renderToString(n, {
      throwOnError: !0,
      displayMode: !0,
      output: ys() ? "mathml" : "htmlAndMathml"
    }).replace(/\n/g, " ").replace(/<annotation.*<\/annotation>/g, "")
  );
}, np = async (t, e) => Pe(await rp(t, e), e), Xn = {
  getRows: Ud,
  sanitizeText: Pe,
  sanitizeTextOrArray: Vd,
  hasBreaks: Xd,
  splitBreaks: Kd,
  lineBreakRegex: ki,
  removeScript: Ga,
  getUrl: Jd,
  evaluate: Xa,
  getMax: Qd,
  getMin: tp
}, ji = {
  /* CLAMP */
  min: {
    r: 0,
    g: 0,
    b: 0,
    s: 0,
    l: 0,
    a: 0
  },
  max: {
    r: 255,
    g: 255,
    b: 255,
    h: 360,
    s: 100,
    l: 100,
    a: 1
  },
  clamp: {
    r: (t) => t >= 255 ? 255 : t < 0 ? 0 : t,
    g: (t) => t >= 255 ? 255 : t < 0 ? 0 : t,
    b: (t) => t >= 255 ? 255 : t < 0 ? 0 : t,
    h: (t) => t % 360,
    s: (t) => t >= 100 ? 100 : t < 0 ? 0 : t,
    l: (t) => t >= 100 ? 100 : t < 0 ? 0 : t,
    a: (t) => t >= 1 ? 1 : t < 0 ? 0 : t
  },
  /* CONVERSION */
  //SOURCE: https://planetcalc.com/7779
  toLinear: (t) => {
    const e = t / 255;
    return t > 0.03928 ? Math.pow((e + 0.055) / 1.055, 2.4) : e / 12.92;
  },
  //SOURCE: https://gist.github.com/mjackson/5311256
  hue2rgb: (t, e, i) => (i < 0 && (i += 1), i > 1 && (i -= 1), i < 1 / 6 ? t + (e - t) * 6 * i : i < 1 / 2 ? e : i < 2 / 3 ? t + (e - t) * (2 / 3 - i) * 6 : t),
  hsl2rgb: ({ h: t, s: e, l: i }, r) => {
    if (!e)
      return i * 2.55;
    t /= 360, e /= 100, i /= 100;
    const n = i < 0.5 ? i * (1 + e) : i + e - i * e, o = 2 * i - n;
    switch (r) {
      case "r":
        return ji.hue2rgb(o, n, t + 1 / 3) * 255;
      case "g":
        return ji.hue2rgb(o, n, t) * 255;
      case "b":
        return ji.hue2rgb(o, n, t - 1 / 3) * 255;
    }
  },
  rgb2hsl: ({ r: t, g: e, b: i }, r) => {
    t /= 255, e /= 255, i /= 255;
    const n = Math.max(t, e, i), o = Math.min(t, e, i), s = (n + o) / 2;
    if (r === "l")
      return s * 100;
    if (n === o)
      return 0;
    const a = n - o, l = s > 0.5 ? a / (2 - n - o) : a / (n + o);
    if (r === "s")
      return l * 100;
    switch (n) {
      case t:
        return ((e - i) / a + (e < i ? 6 : 0)) * 60;
      case e:
        return ((i - t) / a + 2) * 60;
      case i:
        return ((t - e) / a + 4) * 60;
      default:
        return -1;
    }
  }
}, op = ji, sp = {
  /* API */
  clamp: (t, e, i) => e > i ? Math.min(e, Math.max(i, t)) : Math.min(i, Math.max(e, t)),
  round: (t) => Math.round(t * 1e10) / 1e10
}, ap = sp, lp = {
  /* API */
  dec2hex: (t) => {
    const e = Math.round(t).toString(16);
    return e.length > 1 ? e : `0${e}`;
  }
}, hp = lp, cp = {
  channel: op,
  lang: ap,
  unit: hp
}, I = cp, oe = {};
for (let t = 0; t <= 255; t++)
  oe[t] = I.unit.dec2hex(t);
const lt = {
  ALL: 0,
  RGB: 1,
  HSL: 2
};
class up {
  constructor() {
    this.type = lt.ALL;
  }
  /* API */
  get() {
    return this.type;
  }
  set(e) {
    if (this.type && this.type !== e)
      throw new Error("Cannot change both RGB and HSL channels at the same time");
    this.type = e;
  }
  reset() {
    this.type = lt.ALL;
  }
  is(e) {
    return this.type === e;
  }
}
const fp = up;
class dp {
  /* CONSTRUCTOR */
  constructor(e, i) {
    this.color = i, this.changed = !1, this.data = e, this.type = new fp();
  }
  /* API */
  set(e, i) {
    return this.color = i, this.changed = !1, this.data = e, this.type.type = lt.ALL, this;
  }
  /* HELPERS */
  _ensureHSL() {
    const e = this.data, { h: i, s: r, l: n } = e;
    i === void 0 && (e.h = I.channel.rgb2hsl(e, "h")), r === void 0 && (e.s = I.channel.rgb2hsl(e, "s")), n === void 0 && (e.l = I.channel.rgb2hsl(e, "l"));
  }
  _ensureRGB() {
    const e = this.data, { r: i, g: r, b: n } = e;
    i === void 0 && (e.r = I.channel.hsl2rgb(e, "r")), r === void 0 && (e.g = I.channel.hsl2rgb(e, "g")), n === void 0 && (e.b = I.channel.hsl2rgb(e, "b"));
  }
  /* GETTERS */
  get r() {
    const e = this.data, i = e.r;
    return !this.type.is(lt.HSL) && i !== void 0 ? i : (this._ensureHSL(), I.channel.hsl2rgb(e, "r"));
  }
  get g() {
    const e = this.data, i = e.g;
    return !this.type.is(lt.HSL) && i !== void 0 ? i : (this._ensureHSL(), I.channel.hsl2rgb(e, "g"));
  }
  get b() {
    const e = this.data, i = e.b;
    return !this.type.is(lt.HSL) && i !== void 0 ? i : (this._ensureHSL(), I.channel.hsl2rgb(e, "b"));
  }
  get h() {
    const e = this.data, i = e.h;
    return !this.type.is(lt.RGB) && i !== void 0 ? i : (this._ensureRGB(), I.channel.rgb2hsl(e, "h"));
  }
  get s() {
    const e = this.data, i = e.s;
    return !this.type.is(lt.RGB) && i !== void 0 ? i : (this._ensureRGB(), I.channel.rgb2hsl(e, "s"));
  }
  get l() {
    const e = this.data, i = e.l;
    return !this.type.is(lt.RGB) && i !== void 0 ? i : (this._ensureRGB(), I.channel.rgb2hsl(e, "l"));
  }
  get a() {
    return this.data.a;
  }
  /* SETTERS */
  set r(e) {
    this.type.set(lt.RGB), this.changed = !0, this.data.r = e;
  }
  set g(e) {
    this.type.set(lt.RGB), this.changed = !0, this.data.g = e;
  }
  set b(e) {
    this.type.set(lt.RGB), this.changed = !0, this.data.b = e;
  }
  set h(e) {
    this.type.set(lt.HSL), this.changed = !0, this.data.h = e;
  }
  set s(e) {
    this.type.set(lt.HSL), this.changed = !0, this.data.s = e;
  }
  set l(e) {
    this.type.set(lt.HSL), this.changed = !0, this.data.l = e;
  }
  set a(e) {
    this.changed = !0, this.data.a = e;
  }
}
const pp = dp, gp = new pp({ r: 0, g: 0, b: 0, a: 0 }, "transparent"), kr = gp, Ka = {
  /* VARIABLES */
  re: /^#((?:[a-f0-9]{2}){2,4}|[a-f0-9]{3})$/i,
  /* API */
  parse: (t) => {
    if (t.charCodeAt(0) !== 35)
      return;
    const e = t.match(Ka.re);
    if (!e)
      return;
    const i = e[1], r = parseInt(i, 16), n = i.length, o = n % 4 === 0, s = n > 4, a = s ? 1 : 17, l = s ? 8 : 4, h = o ? 0 : -1, u = s ? 255 : 15;
    return kr.set({
      r: (r >> l * (h + 3) & u) * a,
      g: (r >> l * (h + 2) & u) * a,
      b: (r >> l * (h + 1) & u) * a,
      a: o ? (r & u) * a / 255 : 1
    }, t);
  },
  stringify: (t) => {
    const { r: e, g: i, b: r, a: n } = t;
    return n < 1 ? `#${oe[Math.round(e)]}${oe[Math.round(i)]}${oe[Math.round(r)]}${oe[Math.round(n * 255)]}` : `#${oe[Math.round(e)]}${oe[Math.round(i)]}${oe[Math.round(r)]}`;
  }
}, ai = Ka, Ui = {
  /* VARIABLES */
  re: /^hsla?\(\s*?(-?(?:\d+(?:\.\d+)?|(?:\.\d+))(?:e-?\d+)?(?:deg|grad|rad|turn)?)\s*?(?:,|\s)\s*?(-?(?:\d+(?:\.\d+)?|(?:\.\d+))(?:e-?\d+)?%)\s*?(?:,|\s)\s*?(-?(?:\d+(?:\.\d+)?|(?:\.\d+))(?:e-?\d+)?%)(?:\s*?(?:,|\/)\s*?\+?(-?(?:\d+(?:\.\d+)?|(?:\.\d+))(?:e-?\d+)?(%)?))?\s*?\)$/i,
  hueRe: /^(.+?)(deg|grad|rad|turn)$/i,
  /* HELPERS */
  _hue2deg: (t) => {
    const e = t.match(Ui.hueRe);
    if (e) {
      const [, i, r] = e;
      switch (r) {
        case "grad":
          return I.channel.clamp.h(parseFloat(i) * 0.9);
        case "rad":
          return I.channel.clamp.h(parseFloat(i) * 180 / Math.PI);
        case "turn":
          return I.channel.clamp.h(parseFloat(i) * 360);
      }
    }
    return I.channel.clamp.h(parseFloat(t));
  },
  /* API */
  parse: (t) => {
    const e = t.charCodeAt(0);
    if (e !== 104 && e !== 72)
      return;
    const i = t.match(Ui.re);
    if (!i)
      return;
    const [, r, n, o, s, a] = i;
    return kr.set({
      h: Ui._hue2deg(r),
      s: I.channel.clamp.s(parseFloat(n)),
      l: I.channel.clamp.l(parseFloat(o)),
      a: s ? I.channel.clamp.a(a ? parseFloat(s) / 100 : parseFloat(s)) : 1
    }, t);
  },
  stringify: (t) => {
    const { h: e, s: i, l: r, a: n } = t;
    return n < 1 ? `hsla(${I.lang.round(e)}, ${I.lang.round(i)}%, ${I.lang.round(r)}%, ${n})` : `hsl(${I.lang.round(e)}, ${I.lang.round(i)}%, ${I.lang.round(r)}%)`;
  }
}, Ni = Ui, Yi = {
  /* VARIABLES */
  colors: {
    aliceblue: "#f0f8ff",
    antiquewhite: "#faebd7",
    aqua: "#00ffff",
    aquamarine: "#7fffd4",
    azure: "#f0ffff",
    beige: "#f5f5dc",
    bisque: "#ffe4c4",
    black: "#000000",
    blanchedalmond: "#ffebcd",
    blue: "#0000ff",
    blueviolet: "#8a2be2",
    brown: "#a52a2a",
    burlywood: "#deb887",
    cadetblue: "#5f9ea0",
    chartreuse: "#7fff00",
    chocolate: "#d2691e",
    coral: "#ff7f50",
    cornflowerblue: "#6495ed",
    cornsilk: "#fff8dc",
    crimson: "#dc143c",
    cyanaqua: "#00ffff",
    darkblue: "#00008b",
    darkcyan: "#008b8b",
    darkgoldenrod: "#b8860b",
    darkgray: "#a9a9a9",
    darkgreen: "#006400",
    darkgrey: "#a9a9a9",
    darkkhaki: "#bdb76b",
    darkmagenta: "#8b008b",
    darkolivegreen: "#556b2f",
    darkorange: "#ff8c00",
    darkorchid: "#9932cc",
    darkred: "#8b0000",
    darksalmon: "#e9967a",
    darkseagreen: "#8fbc8f",
    darkslateblue: "#483d8b",
    darkslategray: "#2f4f4f",
    darkslategrey: "#2f4f4f",
    darkturquoise: "#00ced1",
    darkviolet: "#9400d3",
    deeppink: "#ff1493",
    deepskyblue: "#00bfff",
    dimgray: "#696969",
    dimgrey: "#696969",
    dodgerblue: "#1e90ff",
    firebrick: "#b22222",
    floralwhite: "#fffaf0",
    forestgreen: "#228b22",
    fuchsia: "#ff00ff",
    gainsboro: "#dcdcdc",
    ghostwhite: "#f8f8ff",
    gold: "#ffd700",
    goldenrod: "#daa520",
    gray: "#808080",
    green: "#008000",
    greenyellow: "#adff2f",
    grey: "#808080",
    honeydew: "#f0fff0",
    hotpink: "#ff69b4",
    indianred: "#cd5c5c",
    indigo: "#4b0082",
    ivory: "#fffff0",
    khaki: "#f0e68c",
    lavender: "#e6e6fa",
    lavenderblush: "#fff0f5",
    lawngreen: "#7cfc00",
    lemonchiffon: "#fffacd",
    lightblue: "#add8e6",
    lightcoral: "#f08080",
    lightcyan: "#e0ffff",
    lightgoldenrodyellow: "#fafad2",
    lightgray: "#d3d3d3",
    lightgreen: "#90ee90",
    lightgrey: "#d3d3d3",
    lightpink: "#ffb6c1",
    lightsalmon: "#ffa07a",
    lightseagreen: "#20b2aa",
    lightskyblue: "#87cefa",
    lightslategray: "#778899",
    lightslategrey: "#778899",
    lightsteelblue: "#b0c4de",
    lightyellow: "#ffffe0",
    lime: "#00ff00",
    limegreen: "#32cd32",
    linen: "#faf0e6",
    magenta: "#ff00ff",
    maroon: "#800000",
    mediumaquamarine: "#66cdaa",
    mediumblue: "#0000cd",
    mediumorchid: "#ba55d3",
    mediumpurple: "#9370db",
    mediumseagreen: "#3cb371",
    mediumslateblue: "#7b68ee",
    mediumspringgreen: "#00fa9a",
    mediumturquoise: "#48d1cc",
    mediumvioletred: "#c71585",
    midnightblue: "#191970",
    mintcream: "#f5fffa",
    mistyrose: "#ffe4e1",
    moccasin: "#ffe4b5",
    navajowhite: "#ffdead",
    navy: "#000080",
    oldlace: "#fdf5e6",
    olive: "#808000",
    olivedrab: "#6b8e23",
    orange: "#ffa500",
    orangered: "#ff4500",
    orchid: "#da70d6",
    palegoldenrod: "#eee8aa",
    palegreen: "#98fb98",
    paleturquoise: "#afeeee",
    palevioletred: "#db7093",
    papayawhip: "#ffefd5",
    peachpuff: "#ffdab9",
    peru: "#cd853f",
    pink: "#ffc0cb",
    plum: "#dda0dd",
    powderblue: "#b0e0e6",
    purple: "#800080",
    rebeccapurple: "#663399",
    red: "#ff0000",
    rosybrown: "#bc8f8f",
    royalblue: "#4169e1",
    saddlebrown: "#8b4513",
    salmon: "#fa8072",
    sandybrown: "#f4a460",
    seagreen: "#2e8b57",
    seashell: "#fff5ee",
    sienna: "#a0522d",
    silver: "#c0c0c0",
    skyblue: "#87ceeb",
    slateblue: "#6a5acd",
    slategray: "#708090",
    slategrey: "#708090",
    snow: "#fffafa",
    springgreen: "#00ff7f",
    tan: "#d2b48c",
    teal: "#008080",
    thistle: "#d8bfd8",
    transparent: "#00000000",
    turquoise: "#40e0d0",
    violet: "#ee82ee",
    wheat: "#f5deb3",
    white: "#ffffff",
    whitesmoke: "#f5f5f5",
    yellow: "#ffff00",
    yellowgreen: "#9acd32"
  },
  /* API */
  parse: (t) => {
    t = t.toLowerCase();
    const e = Yi.colors[t];
    if (e)
      return ai.parse(e);
  },
  stringify: (t) => {
    const e = ai.stringify(t);
    for (const i in Yi.colors)
      if (Yi.colors[i] === e)
        return i;
  }
}, Cs = Yi, Za = {
  /* VARIABLES */
  re: /^rgba?\(\s*?(-?(?:\d+(?:\.\d+)?|(?:\.\d+))(?:e\d+)?(%?))\s*?(?:,|\s)\s*?(-?(?:\d+(?:\.\d+)?|(?:\.\d+))(?:e\d+)?(%?))\s*?(?:,|\s)\s*?(-?(?:\d+(?:\.\d+)?|(?:\.\d+))(?:e\d+)?(%?))(?:\s*?(?:,|\/)\s*?\+?(-?(?:\d+(?:\.\d+)?|(?:\.\d+))(?:e\d+)?(%?)))?\s*?\)$/i,
  /* API */
  parse: (t) => {
    const e = t.charCodeAt(0);
    if (e !== 114 && e !== 82)
      return;
    const i = t.match(Za.re);
    if (!i)
      return;
    const [, r, n, o, s, a, l, h, u] = i;
    return kr.set({
      r: I.channel.clamp.r(n ? parseFloat(r) * 2.55 : parseFloat(r)),
      g: I.channel.clamp.g(s ? parseFloat(o) * 2.55 : parseFloat(o)),
      b: I.channel.clamp.b(l ? parseFloat(a) * 2.55 : parseFloat(a)),
      a: h ? I.channel.clamp.a(u ? parseFloat(h) / 100 : parseFloat(h)) : 1
    }, t);
  },
  stringify: (t) => {
    const { r: e, g: i, b: r, a: n } = t;
    return n < 1 ? `rgba(${I.lang.round(e)}, ${I.lang.round(i)}, ${I.lang.round(r)}, ${I.lang.round(n)})` : `rgb(${I.lang.round(e)}, ${I.lang.round(i)}, ${I.lang.round(r)})`;
  }
}, Ri = Za, mp = {
  /* VARIABLES */
  format: {
    keyword: Cs,
    hex: ai,
    rgb: Ri,
    rgba: Ri,
    hsl: Ni,
    hsla: Ni
  },
  /* API */
  parse: (t) => {
    if (typeof t != "string")
      return t;
    const e = ai.parse(t) || Ri.parse(t) || Ni.parse(t) || Cs.parse(t);
    if (e)
      return e;
    throw new Error(`Unsupported color format: "${t}"`);
  },
  stringify: (t) => !t.changed && t.color ? t.color : t.type.is(lt.HSL) || t.data.r === void 0 ? Ni.stringify(t) : t.a < 1 || !Number.isInteger(t.r) || !Number.isInteger(t.g) || !Number.isInteger(t.b) ? Ri.stringify(t) : ai.stringify(t)
}, jt = mp, yp = (t, e) => {
  const i = jt.parse(t);
  for (const r in e)
    i[r] = I.channel.clamp[r](e[r]);
  return jt.stringify(i);
}, Ja = yp, _p = (t, e, i = 0, r = 1) => {
  if (typeof t != "number")
    return Ja(t, { a: e });
  const n = kr.set({
    r: I.channel.clamp.r(t),
    g: I.channel.clamp.g(e),
    b: I.channel.clamp.b(i),
    a: I.channel.clamp.a(r)
  });
  return jt.stringify(n);
}, li = _p, Cp = (t) => {
  const { r: e, g: i, b: r } = jt.parse(t), n = 0.2126 * I.channel.toLinear(e) + 0.7152 * I.channel.toLinear(i) + 0.0722 * I.channel.toLinear(r);
  return I.lang.round(n);
}, xp = Cp, bp = (t) => xp(t) >= 0.5, Tp = bp, Sp = (t) => !Tp(t), vi = Sp, kp = (t, e, i) => {
  const r = jt.parse(t), n = r[e], o = I.channel.clamp[e](n + i);
  return n !== o && (r[e] = o), jt.stringify(r);
}, Qa = kp, vp = (t, e) => Qa(t, "l", e), w = vp, wp = (t, e) => Qa(t, "l", -e), O = wp, Bp = (t, e) => {
  const i = jt.parse(t), r = {};
  for (const n in e)
    e[n] && (r[n] = i[n] + e[n]);
  return Ja(t, r);
}, g = Bp, Ap = (t, e, i = 50) => {
  const { r, g: n, b: o, a: s } = jt.parse(t), { r: a, g: l, b: h, a: u } = jt.parse(e), d = i / 100, c = d * 2 - 1, p = s - u, S = ((c * p === -1 ? c : (c + p) / (1 + c * p)) + 1) / 2, M = 1 - S, q = r * S + a * M, T = n * S + l * M, U = o * S + h * M, W = s * d + u * (1 - d);
  return li(q, T, U, W);
}, Fp = Ap, Lp = (t, e = 100) => {
  const i = jt.parse(t);
  return i.r = 255 - i.r, i.g = 255 - i.g, i.b = 255 - i.b, Fp(i, t, e);
}, _ = Lp, dt = (t, e) => e ? g(t, { s: -40, l: 10 }) : g(t, { s: -40, l: -10 }), vr = "#ffffff", wr = "#f2f2f2";
let Ep = class {
  constructor() {
    this.background = "#f4f4f4", this.primaryColor = "#fff4dd", this.noteBkgColor = "#fff5ad", this.noteTextColor = "#333", this.THEME_COLOR_LIMIT = 12, this.fontFamily = '"trebuchet ms", verdana, arial, sans-serif', this.fontSize = "16px";
  }
  updateColors() {
    var i, r, n, o, s, a, l, h, u, d, c;
    if (this.primaryTextColor = this.primaryTextColor || (this.darkMode ? "#eee" : "#333"), this.secondaryColor = this.secondaryColor || g(this.primaryColor, { h: -120 }), this.tertiaryColor = this.tertiaryColor || g(this.primaryColor, { h: 180, l: 5 }), this.primaryBorderColor = this.primaryBorderColor || dt(this.primaryColor, this.darkMode), this.secondaryBorderColor = this.secondaryBorderColor || dt(this.secondaryColor, this.darkMode), this.tertiaryBorderColor = this.tertiaryBorderColor || dt(this.tertiaryColor, this.darkMode), this.noteBorderColor = this.noteBorderColor || dt(this.noteBkgColor, this.darkMode), this.noteBkgColor = this.noteBkgColor || "#fff5ad", this.noteTextColor = this.noteTextColor || "#333", this.secondaryTextColor = this.secondaryTextColor || _(this.secondaryColor), this.tertiaryTextColor = this.tertiaryTextColor || _(this.tertiaryColor), this.lineColor = this.lineColor || _(this.background), this.arrowheadColor = this.arrowheadColor || _(this.background), this.textColor = this.textColor || this.primaryTextColor, this.border2 = this.border2 || this.tertiaryBorderColor, this.nodeBkg = this.nodeBkg || this.primaryColor, this.mainBkg = this.mainBkg || this.primaryColor, this.nodeBorder = this.nodeBorder || this.primaryBorderColor, this.clusterBkg = this.clusterBkg || this.tertiaryColor, this.clusterBorder = this.clusterBorder || this.tertiaryBorderColor, this.defaultLinkColor = this.defaultLinkColor || this.lineColor, this.titleColor = this.titleColor || this.tertiaryTextColor, this.edgeLabelBackground = this.edgeLabelBackground || (this.darkMode ? O(this.secondaryColor, 30) : this.secondaryColor), this.nodeTextColor = this.nodeTextColor || this.primaryTextColor, this.actorBorder = this.actorBorder || this.primaryBorderColor, this.actorBkg = this.actorBkg || this.mainBkg, this.actorTextColor = this.actorTextColor || this.primaryTextColor, this.actorLineColor = this.actorLineColor || "grey", this.labelBoxBkgColor = this.labelBoxBkgColor || this.actorBkg, this.signalColor = this.signalColor || this.textColor, this.signalTextColor = this.signalTextColor || this.textColor, this.labelBoxBorderColor = this.labelBoxBorderColor || this.actorBorder, this.labelTextColor = this.labelTextColor || this.actorTextColor, this.loopTextColor = this.loopTextColor || this.actorTextColor, this.activationBorderColor = this.activationBorderColor || O(this.secondaryColor, 10), this.activationBkgColor = this.activationBkgColor || this.secondaryColor, this.sequenceNumberColor = this.sequenceNumberColor || _(this.lineColor), this.sectionBkgColor = this.sectionBkgColor || this.tertiaryColor, this.altSectionBkgColor = this.altSectionBkgColor || "white", this.sectionBkgColor = this.sectionBkgColor || this.secondaryColor, this.sectionBkgColor2 = this.sectionBkgColor2 || this.primaryColor, this.excludeBkgColor = this.excludeBkgColor || "#eeeeee", this.taskBorderColor = this.taskBorderColor || this.primaryBorderColor, this.taskBkgColor = this.taskBkgColor || this.primaryColor, this.activeTaskBorderColor = this.activeTaskBorderColor || this.primaryColor, this.activeTaskBkgColor = this.activeTaskBkgColor || w(this.primaryColor, 23), this.gridColor = this.gridColor || "lightgrey", this.doneTaskBkgColor = this.doneTaskBkgColor || "lightgrey", this.doneTaskBorderColor = this.doneTaskBorderColor || "grey", this.critBorderColor = this.critBorderColor || "#ff8888", this.critBkgColor = this.critBkgColor || "red", this.todayLineColor = this.todayLineColor || "red", this.taskTextColor = this.taskTextColor || this.textColor, this.taskTextOutsideColor = this.taskTextOutsideColor || this.textColor, this.taskTextLightColor = this.taskTextLightColor || this.textColor, this.taskTextColor = this.taskTextColor || this.primaryTextColor, this.taskTextDarkColor = this.taskTextDarkColor || this.textColor, this.taskTextClickableColor = this.taskTextClickableColor || "#003163", this.personBorder = this.personBorder || this.primaryBorderColor, this.personBkg = this.personBkg || this.mainBkg, this.transitionColor = this.transitionColor || this.lineColor, this.transitionLabelColor = this.transitionLabelColor || this.textColor, this.stateLabelColor = this.stateLabelColor || this.stateBkg || this.primaryTextColor, this.stateBkg = this.stateBkg || this.mainBkg, this.labelBackgroundColor = this.labelBackgroundColor || this.stateBkg, this.compositeBackground = this.compositeBackground || this.background || this.tertiaryColor, this.altBackground = this.altBackground || this.tertiaryColor, this.compositeTitleBackground = this.compositeTitleBackground || this.mainBkg, this.compositeBorder = this.compositeBorder || this.nodeBorder, this.innerEndBackground = this.nodeBorder, this.errorBkgColor = this.errorBkgColor || this.tertiaryColor, this.errorTextColor = this.errorTextColor || this.tertiaryTextColor, this.transitionColor = this.transitionColor || this.lineColor, this.specialStateColor = this.lineColor, this.cScale0 = this.cScale0 || this.primaryColor, this.cScale1 = this.cScale1 || this.secondaryColor, this.cScale2 = this.cScale2 || this.tertiaryColor, this.cScale3 = this.cScale3 || g(this.primaryColor, { h: 30 }), this.cScale4 = this.cScale4 || g(this.primaryColor, { h: 60 }), this.cScale5 = this.cScale5 || g(this.primaryColor, { h: 90 }), this.cScale6 = this.cScale6 || g(this.primaryColor, { h: 120 }), this.cScale7 = this.cScale7 || g(this.primaryColor, { h: 150 }), this.cScale8 = this.cScale8 || g(this.primaryColor, { h: 210, l: 150 }), this.cScale9 = this.cScale9 || g(this.primaryColor, { h: 270 }), this.cScale10 = this.cScale10 || g(this.primaryColor, { h: 300 }), this.cScale11 = this.cScale11 || g(this.primaryColor, { h: 330 }), this.darkMode)
      for (let p = 0; p < this.THEME_COLOR_LIMIT; p++)
        this["cScale" + p] = O(this["cScale" + p], 75);
    else
      for (let p = 0; p < this.THEME_COLOR_LIMIT; p++)
        this["cScale" + p] = O(this["cScale" + p], 25);
    for (let p = 0; p < this.THEME_COLOR_LIMIT; p++)
      this["cScaleInv" + p] = this["cScaleInv" + p] || _(this["cScale" + p]);
    for (let p = 0; p < this.THEME_COLOR_LIMIT; p++)
      this.darkMode ? this["cScalePeer" + p] = this["cScalePeer" + p] || w(this["cScale" + p], 10) : this["cScalePeer" + p] = this["cScalePeer" + p] || O(this["cScale" + p], 10);
    this.scaleLabelColor = this.scaleLabelColor || this.labelTextColor;
    for (let p = 0; p < this.THEME_COLOR_LIMIT; p++)
      this["cScaleLabel" + p] = this["cScaleLabel" + p] || this.scaleLabelColor;
    const e = this.darkMode ? -4 : -1;
    for (let p = 0; p < 5; p++)
      this["surface" + p] = this["surface" + p] || g(this.mainBkg, { h: 180, s: -15, l: e * (5 + p * 3) }), this["surfacePeer" + p] = this["surfacePeer" + p] || g(this.mainBkg, { h: 180, s: -15, l: e * (8 + p * 3) });
    this.classText = this.classText || this.textColor, this.fillType0 = this.fillType0 || this.primaryColor, this.fillType1 = this.fillType1 || this.secondaryColor, this.fillType2 = this.fillType2 || g(this.primaryColor, { h: 64 }), this.fillType3 = this.fillType3 || g(this.secondaryColor, { h: 64 }), this.fillType4 = this.fillType4 || g(this.primaryColor, { h: -64 }), this.fillType5 = this.fillType5 || g(this.secondaryColor, { h: -64 }), this.fillType6 = this.fillType6 || g(this.primaryColor, { h: 128 }), this.fillType7 = this.fillType7 || g(this.secondaryColor, { h: 128 }), this.pie1 = this.pie1 || this.primaryColor, this.pie2 = this.pie2 || this.secondaryColor, this.pie3 = this.pie3 || this.tertiaryColor, this.pie4 = this.pie4 || g(this.primaryColor, { l: -10 }), this.pie5 = this.pie5 || g(this.secondaryColor, { l: -10 }), this.pie6 = this.pie6 || g(this.tertiaryColor, { l: -10 }), this.pie7 = this.pie7 || g(this.primaryColor, { h: 60, l: -10 }), this.pie8 = this.pie8 || g(this.primaryColor, { h: -60, l: -10 }), this.pie9 = this.pie9 || g(this.primaryColor, { h: 120, l: 0 }), this.pie10 = this.pie10 || g(this.primaryColor, { h: 60, l: -20 }), this.pie11 = this.pie11 || g(this.primaryColor, { h: -60, l: -20 }), this.pie12 = this.pie12 || g(this.primaryColor, { h: 120, l: -10 }), this.pieTitleTextSize = this.pieTitleTextSize || "25px", this.pieTitleTextColor = this.pieTitleTextColor || this.taskTextDarkColor, this.pieSectionTextSize = this.pieSectionTextSize || "17px", this.pieSectionTextColor = this.pieSectionTextColor || this.textColor, this.pieLegendTextSize = this.pieLegendTextSize || "17px", this.pieLegendTextColor = this.pieLegendTextColor || this.taskTextDarkColor, this.pieStrokeColor = this.pieStrokeColor || "black", this.pieStrokeWidth = this.pieStrokeWidth || "2px", this.pieOuterStrokeWidth = this.pieOuterStrokeWidth || "2px", this.pieOuterStrokeColor = this.pieOuterStrokeColor || "black", this.pieOpacity = this.pieOpacity || "0.7", this.quadrant1Fill = this.quadrant1Fill || this.primaryColor, this.quadrant2Fill = this.quadrant2Fill || g(this.primaryColor, { r: 5, g: 5, b: 5 }), this.quadrant3Fill = this.quadrant3Fill || g(this.primaryColor, { r: 10, g: 10, b: 10 }), this.quadrant4Fill = this.quadrant4Fill || g(this.primaryColor, { r: 15, g: 15, b: 15 }), this.quadrant1TextFill = this.quadrant1TextFill || this.primaryTextColor, this.quadrant2TextFill = this.quadrant2TextFill || g(this.primaryTextColor, { r: -5, g: -5, b: -5 }), this.quadrant3TextFill = this.quadrant3TextFill || g(this.primaryTextColor, { r: -10, g: -10, b: -10 }), this.quadrant4TextFill = this.quadrant4TextFill || g(this.primaryTextColor, { r: -15, g: -15, b: -15 }), this.quadrantPointFill = this.quadrantPointFill || vi(this.quadrant1Fill) ? w(this.quadrant1Fill) : O(this.quadrant1Fill), this.quadrantPointTextFill = this.quadrantPointTextFill || this.primaryTextColor, this.quadrantXAxisTextFill = this.quadrantXAxisTextFill || this.primaryTextColor, this.quadrantYAxisTextFill = this.quadrantYAxisTextFill || this.primaryTextColor, this.quadrantInternalBorderStrokeFill = this.quadrantInternalBorderStrokeFill || this.primaryBorderColor, this.quadrantExternalBorderStrokeFill = this.quadrantExternalBorderStrokeFill || this.primaryBorderColor, this.quadrantTitleFill = this.quadrantTitleFill || this.primaryTextColor, this.xyChart = {
      backgroundColor: ((i = this.xyChart) == null ? void 0 : i.backgroundColor) || this.background,
      titleColor: ((r = this.xyChart) == null ? void 0 : r.titleColor) || this.primaryTextColor,
      xAxisTitleColor: ((n = this.xyChart) == null ? void 0 : n.xAxisTitleColor) || this.primaryTextColor,
      xAxisLabelColor: ((o = this.xyChart) == null ? void 0 : o.xAxisLabelColor) || this.primaryTextColor,
      xAxisTickColor: ((s = this.xyChart) == null ? void 0 : s.xAxisTickColor) || this.primaryTextColor,
      xAxisLineColor: ((a = this.xyChart) == null ? void 0 : a.xAxisLineColor) || this.primaryTextColor,
      yAxisTitleColor: ((l = this.xyChart) == null ? void 0 : l.yAxisTitleColor) || this.primaryTextColor,
      yAxisLabelColor: ((h = this.xyChart) == null ? void 0 : h.yAxisLabelColor) || this.primaryTextColor,
      yAxisTickColor: ((u = this.xyChart) == null ? void 0 : u.yAxisTickColor) || this.primaryTextColor,
      yAxisLineColor: ((d = this.xyChart) == null ? void 0 : d.yAxisLineColor) || this.primaryTextColor,
      plotColorPalette: ((c = this.xyChart) == null ? void 0 : c.plotColorPalette) || "#FFF4DD,#FFD8B1,#FFA07A,#ECEFF1,#D6DBDF,#C3E0A8,#FFB6A4,#FFD74D,#738FA7,#FFFFF0"
    }, this.requirementBackground = this.requirementBackground || this.primaryColor, this.requirementBorderColor = this.requirementBorderColor || this.primaryBorderColor, this.requirementBorderSize = this.requirementBorderSize || "1", this.requirementTextColor = this.requirementTextColor || this.primaryTextColor, this.relationColor = this.relationColor || this.lineColor, this.relationLabelBackground = this.relationLabelBackground || (this.darkMode ? O(this.secondaryColor, 30) : this.secondaryColor), this.relationLabelColor = this.relationLabelColor || this.actorTextColor, this.git0 = this.git0 || this.primaryColor, this.git1 = this.git1 || this.secondaryColor, this.git2 = this.git2 || this.tertiaryColor, this.git3 = this.git3 || g(this.primaryColor, { h: -30 }), this.git4 = this.git4 || g(this.primaryColor, { h: -60 }), this.git5 = this.git5 || g(this.primaryColor, { h: -90 }), this.git6 = this.git6 || g(this.primaryColor, { h: 60 }), this.git7 = this.git7 || g(this.primaryColor, { h: 120 }), this.darkMode ? (this.git0 = w(this.git0, 25), this.git1 = w(this.git1, 25), this.git2 = w(this.git2, 25), this.git3 = w(this.git3, 25), this.git4 = w(this.git4, 25), this.git5 = w(this.git5, 25), this.git6 = w(this.git6, 25), this.git7 = w(this.git7, 25)) : (this.git0 = O(this.git0, 25), this.git1 = O(this.git1, 25), this.git2 = O(this.git2, 25), this.git3 = O(this.git3, 25), this.git4 = O(this.git4, 25), this.git5 = O(this.git5, 25), this.git6 = O(this.git6, 25), this.git7 = O(this.git7, 25)), this.gitInv0 = this.gitInv0 || _(this.git0), this.gitInv1 = this.gitInv1 || _(this.git1), this.gitInv2 = this.gitInv2 || _(this.git2), this.gitInv3 = this.gitInv3 || _(this.git3), this.gitInv4 = this.gitInv4 || _(this.git4), this.gitInv5 = this.gitInv5 || _(this.git5), this.gitInv6 = this.gitInv6 || _(this.git6), this.gitInv7 = this.gitInv7 || _(this.git7), this.branchLabelColor = this.branchLabelColor || (this.darkMode ? "black" : this.labelTextColor), this.gitBranchLabel0 = this.gitBranchLabel0 || this.branchLabelColor, this.gitBranchLabel1 = this.gitBranchLabel1 || this.branchLabelColor, this.gitBranchLabel2 = this.gitBranchLabel2 || this.branchLabelColor, this.gitBranchLabel3 = this.gitBranchLabel3 || this.branchLabelColor, this.gitBranchLabel4 = this.gitBranchLabel4 || this.branchLabelColor, this.gitBranchLabel5 = this.gitBranchLabel5 || this.branchLabelColor, this.gitBranchLabel6 = this.gitBranchLabel6 || this.branchLabelColor, this.gitBranchLabel7 = this.gitBranchLabel7 || this.branchLabelColor, this.tagLabelColor = this.tagLabelColor || this.primaryTextColor, this.tagLabelBackground = this.tagLabelBackground || this.primaryColor, this.tagLabelBorder = this.tagBorder || this.primaryBorderColor, this.tagLabelFontSize = this.tagLabelFontSize || "10px", this.commitLabelColor = this.commitLabelColor || this.secondaryTextColor, this.commitLabelBackground = this.commitLabelBackground || this.secondaryColor, this.commitLabelFontSize = this.commitLabelFontSize || "10px", this.attributeBackgroundColorOdd = this.attributeBackgroundColorOdd || vr, this.attributeBackgroundColorEven = this.attributeBackgroundColorEven || wr;
  }
  calculate(e) {
    if (typeof e != "object") {
      this.updateColors();
      return;
    }
    const i = Object.keys(e);
    i.forEach((r) => {
      this[r] = e[r];
    }), this.updateColors(), i.forEach((r) => {
      this[r] = e[r];
    });
  }
};
const Op = (t) => {
  const e = new Ep();
  return e.calculate(t), e;
};
let Mp = class {
  constructor() {
    this.background = "#333", this.primaryColor = "#1f2020", this.secondaryColor = w(this.primaryColor, 16), this.tertiaryColor = g(this.primaryColor, { h: -160 }), this.primaryBorderColor = _(this.background), this.secondaryBorderColor = dt(this.secondaryColor, this.darkMode), this.tertiaryBorderColor = dt(this.tertiaryColor, this.darkMode), this.primaryTextColor = _(this.primaryColor), this.secondaryTextColor = _(this.secondaryColor), this.tertiaryTextColor = _(this.tertiaryColor), this.lineColor = _(this.background), this.textColor = _(this.background), this.mainBkg = "#1f2020", this.secondBkg = "calculated", this.mainContrastColor = "lightgrey", this.darkTextColor = w(_("#323D47"), 10), this.lineColor = "calculated", this.border1 = "#81B1DB", this.border2 = li(255, 255, 255, 0.25), this.arrowheadColor = "calculated", this.fontFamily = '"trebuchet ms", verdana, arial, sans-serif', this.fontSize = "16px", this.labelBackground = "#181818", this.textColor = "#ccc", this.THEME_COLOR_LIMIT = 12, this.nodeBkg = "calculated", this.nodeBorder = "calculated", this.clusterBkg = "calculated", this.clusterBorder = "calculated", this.defaultLinkColor = "calculated", this.titleColor = "#F9FFFE", this.edgeLabelBackground = "calculated", this.actorBorder = "calculated", this.actorBkg = "calculated", this.actorTextColor = "calculated", this.actorLineColor = "calculated", this.signalColor = "calculated", this.signalTextColor = "calculated", this.labelBoxBkgColor = "calculated", this.labelBoxBorderColor = "calculated", this.labelTextColor = "calculated", this.loopTextColor = "calculated", this.noteBorderColor = "calculated", this.noteBkgColor = "#fff5ad", this.noteTextColor = "calculated", this.activationBorderColor = "calculated", this.activationBkgColor = "calculated", this.sequenceNumberColor = "black", this.sectionBkgColor = O("#EAE8D9", 30), this.altSectionBkgColor = "calculated", this.sectionBkgColor2 = "#EAE8D9", this.excludeBkgColor = O(this.sectionBkgColor, 10), this.taskBorderColor = li(255, 255, 255, 70), this.taskBkgColor = "calculated", this.taskTextColor = "calculated", this.taskTextLightColor = "calculated", this.taskTextOutsideColor = "calculated", this.taskTextClickableColor = "#003163", this.activeTaskBorderColor = li(255, 255, 255, 50), this.activeTaskBkgColor = "#81B1DB", this.gridColor = "calculated", this.doneTaskBkgColor = "calculated", this.doneTaskBorderColor = "grey", this.critBorderColor = "#E83737", this.critBkgColor = "#E83737", this.taskTextDarkColor = "calculated", this.todayLineColor = "#DB5757", this.personBorder = this.primaryBorderColor, this.personBkg = this.mainBkg, this.labelColor = "calculated", this.errorBkgColor = "#a44141", this.errorTextColor = "#ddd";
  }
  updateColors() {
    var e, i, r, n, o, s, a, l, h, u, d;
    this.secondBkg = w(this.mainBkg, 16), this.lineColor = this.mainContrastColor, this.arrowheadColor = this.mainContrastColor, this.nodeBkg = this.mainBkg, this.nodeBorder = this.border1, this.clusterBkg = this.secondBkg, this.clusterBorder = this.border2, this.defaultLinkColor = this.lineColor, this.edgeLabelBackground = w(this.labelBackground, 25), this.actorBorder = this.border1, this.actorBkg = this.mainBkg, this.actorTextColor = this.mainContrastColor, this.actorLineColor = this.mainContrastColor, this.signalColor = this.mainContrastColor, this.signalTextColor = this.mainContrastColor, this.labelBoxBkgColor = this.actorBkg, this.labelBoxBorderColor = this.actorBorder, this.labelTextColor = this.mainContrastColor, this.loopTextColor = this.mainContrastColor, this.noteBorderColor = this.secondaryBorderColor, this.noteBkgColor = this.secondBkg, this.noteTextColor = this.secondaryTextColor, this.activationBorderColor = this.border1, this.activationBkgColor = this.secondBkg, this.altSectionBkgColor = this.background, this.taskBkgColor = w(this.mainBkg, 23), this.taskTextColor = this.darkTextColor, this.taskTextLightColor = this.mainContrastColor, this.taskTextOutsideColor = this.taskTextLightColor, this.gridColor = this.mainContrastColor, this.doneTaskBkgColor = this.mainContrastColor, this.taskTextDarkColor = this.darkTextColor, this.transitionColor = this.transitionColor || this.lineColor, this.transitionLabelColor = this.transitionLabelColor || this.textColor, this.stateLabelColor = this.stateLabelColor || this.stateBkg || this.primaryTextColor, this.stateBkg = this.stateBkg || this.mainBkg, this.labelBackgroundColor = this.labelBackgroundColor || this.stateBkg, this.compositeBackground = this.compositeBackground || this.background || this.tertiaryColor, this.altBackground = this.altBackground || "#555", this.compositeTitleBackground = this.compositeTitleBackground || this.mainBkg, this.compositeBorder = this.compositeBorder || this.nodeBorder, this.innerEndBackground = this.primaryBorderColor, this.specialStateColor = "#f4f4f4", this.errorBkgColor = this.errorBkgColor || this.tertiaryColor, this.errorTextColor = this.errorTextColor || this.tertiaryTextColor, this.fillType0 = this.primaryColor, this.fillType1 = this.secondaryColor, this.fillType2 = g(this.primaryColor, { h: 64 }), this.fillType3 = g(this.secondaryColor, { h: 64 }), this.fillType4 = g(this.primaryColor, { h: -64 }), this.fillType5 = g(this.secondaryColor, { h: -64 }), this.fillType6 = g(this.primaryColor, { h: 128 }), this.fillType7 = g(this.secondaryColor, { h: 128 }), this.cScale1 = this.cScale1 || "#0b0000", this.cScale2 = this.cScale2 || "#4d1037", this.cScale3 = this.cScale3 || "#3f5258", this.cScale4 = this.cScale4 || "#4f2f1b", this.cScale5 = this.cScale5 || "#6e0a0a", this.cScale6 = this.cScale6 || "#3b0048", this.cScale7 = this.cScale7 || "#995a01", this.cScale8 = this.cScale8 || "#154706", this.cScale9 = this.cScale9 || "#161722", this.cScale10 = this.cScale10 || "#00296f", this.cScale11 = this.cScale11 || "#01629c", this.cScale12 = this.cScale12 || "#010029", this.cScale0 = this.cScale0 || this.primaryColor, this.cScale1 = this.cScale1 || this.secondaryColor, this.cScale2 = this.cScale2 || this.tertiaryColor, this.cScale3 = this.cScale3 || g(this.primaryColor, { h: 30 }), this.cScale4 = this.cScale4 || g(this.primaryColor, { h: 60 }), this.cScale5 = this.cScale5 || g(this.primaryColor, { h: 90 }), this.cScale6 = this.cScale6 || g(this.primaryColor, { h: 120 }), this.cScale7 = this.cScale7 || g(this.primaryColor, { h: 150 }), this.cScale8 = this.cScale8 || g(this.primaryColor, { h: 210 }), this.cScale9 = this.cScale9 || g(this.primaryColor, { h: 270 }), this.cScale10 = this.cScale10 || g(this.primaryColor, { h: 300 }), this.cScale11 = this.cScale11 || g(this.primaryColor, { h: 330 });
    for (let c = 0; c < this.THEME_COLOR_LIMIT; c++)
      this["cScaleInv" + c] = this["cScaleInv" + c] || _(this["cScale" + c]);
    for (let c = 0; c < this.THEME_COLOR_LIMIT; c++)
      this["cScalePeer" + c] = this["cScalePeer" + c] || w(this["cScale" + c], 10);
    for (let c = 0; c < 5; c++)
      this["surface" + c] = this["surface" + c] || g(this.mainBkg, { h: 30, s: -30, l: -(-10 + c * 4) }), this["surfacePeer" + c] = this["surfacePeer" + c] || g(this.mainBkg, { h: 30, s: -30, l: -(-7 + c * 4) });
    this.scaleLabelColor = this.scaleLabelColor || (this.darkMode ? "black" : this.labelTextColor);
    for (let c = 0; c < this.THEME_COLOR_LIMIT; c++)
      this["cScaleLabel" + c] = this["cScaleLabel" + c] || this.scaleLabelColor;
    for (let c = 0; c < this.THEME_COLOR_LIMIT; c++)
      this["pie" + c] = this["cScale" + c];
    this.pieTitleTextSize = this.pieTitleTextSize || "25px", this.pieTitleTextColor = this.pieTitleTextColor || this.taskTextDarkColor, this.pieSectionTextSize = this.pieSectionTextSize || "17px", this.pieSectionTextColor = this.pieSectionTextColor || this.textColor, this.pieLegendTextSize = this.pieLegendTextSize || "17px", this.pieLegendTextColor = this.pieLegendTextColor || this.taskTextDarkColor, this.pieStrokeColor = this.pieStrokeColor || "black", this.pieStrokeWidth = this.pieStrokeWidth || "2px", this.pieOuterStrokeWidth = this.pieOuterStrokeWidth || "2px", this.pieOuterStrokeColor = this.pieOuterStrokeColor || "black", this.pieOpacity = this.pieOpacity || "0.7", this.quadrant1Fill = this.quadrant1Fill || this.primaryColor, this.quadrant2Fill = this.quadrant2Fill || g(this.primaryColor, { r: 5, g: 5, b: 5 }), this.quadrant3Fill = this.quadrant3Fill || g(this.primaryColor, { r: 10, g: 10, b: 10 }), this.quadrant4Fill = this.quadrant4Fill || g(this.primaryColor, { r: 15, g: 15, b: 15 }), this.quadrant1TextFill = this.quadrant1TextFill || this.primaryTextColor, this.quadrant2TextFill = this.quadrant2TextFill || g(this.primaryTextColor, { r: -5, g: -5, b: -5 }), this.quadrant3TextFill = this.quadrant3TextFill || g(this.primaryTextColor, { r: -10, g: -10, b: -10 }), this.quadrant4TextFill = this.quadrant4TextFill || g(this.primaryTextColor, { r: -15, g: -15, b: -15 }), this.quadrantPointFill = this.quadrantPointFill || vi(this.quadrant1Fill) ? w(this.quadrant1Fill) : O(this.quadrant1Fill), this.quadrantPointTextFill = this.quadrantPointTextFill || this.primaryTextColor, this.quadrantXAxisTextFill = this.quadrantXAxisTextFill || this.primaryTextColor, this.quadrantYAxisTextFill = this.quadrantYAxisTextFill || this.primaryTextColor, this.quadrantInternalBorderStrokeFill = this.quadrantInternalBorderStrokeFill || this.primaryBorderColor, this.quadrantExternalBorderStrokeFill = this.quadrantExternalBorderStrokeFill || this.primaryBorderColor, this.quadrantTitleFill = this.quadrantTitleFill || this.primaryTextColor, this.xyChart = {
      backgroundColor: ((e = this.xyChart) == null ? void 0 : e.backgroundColor) || this.background,
      titleColor: ((i = this.xyChart) == null ? void 0 : i.titleColor) || this.primaryTextColor,
      xAxisTitleColor: ((r = this.xyChart) == null ? void 0 : r.xAxisTitleColor) || this.primaryTextColor,
      xAxisLabelColor: ((n = this.xyChart) == null ? void 0 : n.xAxisLabelColor) || this.primaryTextColor,
      xAxisTickColor: ((o = this.xyChart) == null ? void 0 : o.xAxisTickColor) || this.primaryTextColor,
      xAxisLineColor: ((s = this.xyChart) == null ? void 0 : s.xAxisLineColor) || this.primaryTextColor,
      yAxisTitleColor: ((a = this.xyChart) == null ? void 0 : a.yAxisTitleColor) || this.primaryTextColor,
      yAxisLabelColor: ((l = this.xyChart) == null ? void 0 : l.yAxisLabelColor) || this.primaryTextColor,
      yAxisTickColor: ((h = this.xyChart) == null ? void 0 : h.yAxisTickColor) || this.primaryTextColor,
      yAxisLineColor: ((u = this.xyChart) == null ? void 0 : u.yAxisLineColor) || this.primaryTextColor,
      plotColorPalette: ((d = this.xyChart) == null ? void 0 : d.plotColorPalette) || "#3498db,#2ecc71,#e74c3c,#f1c40f,#bdc3c7,#ffffff,#34495e,#9b59b6,#1abc9c,#e67e22"
    }, this.classText = this.primaryTextColor, this.requirementBackground = this.requirementBackground || this.primaryColor, this.requirementBorderColor = this.requirementBorderColor || this.primaryBorderColor, this.requirementBorderSize = this.requirementBorderSize || "1", this.requirementTextColor = this.requirementTextColor || this.primaryTextColor, this.relationColor = this.relationColor || this.lineColor, this.relationLabelBackground = this.relationLabelBackground || (this.darkMode ? O(this.secondaryColor, 30) : this.secondaryColor), this.relationLabelColor = this.relationLabelColor || this.actorTextColor, this.git0 = w(this.secondaryColor, 20), this.git1 = w(this.pie2 || this.secondaryColor, 20), this.git2 = w(this.pie3 || this.tertiaryColor, 20), this.git3 = w(this.pie4 || g(this.primaryColor, { h: -30 }), 20), this.git4 = w(this.pie5 || g(this.primaryColor, { h: -60 }), 20), this.git5 = w(this.pie6 || g(this.primaryColor, { h: -90 }), 10), this.git6 = w(this.pie7 || g(this.primaryColor, { h: 60 }), 10), this.git7 = w(this.pie8 || g(this.primaryColor, { h: 120 }), 20), this.gitInv0 = this.gitInv0 || _(this.git0), this.gitInv1 = this.gitInv1 || _(this.git1), this.gitInv2 = this.gitInv2 || _(this.git2), this.gitInv3 = this.gitInv3 || _(this.git3), this.gitInv4 = this.gitInv4 || _(this.git4), this.gitInv5 = this.gitInv5 || _(this.git5), this.gitInv6 = this.gitInv6 || _(this.git6), this.gitInv7 = this.gitInv7 || _(this.git7), this.gitBranchLabel0 = this.gitBranchLabel0 || _(this.labelTextColor), this.gitBranchLabel1 = this.gitBranchLabel1 || this.labelTextColor, this.gitBranchLabel2 = this.gitBranchLabel2 || this.labelTextColor, this.gitBranchLabel3 = this.gitBranchLabel3 || _(this.labelTextColor), this.gitBranchLabel4 = this.gitBranchLabel4 || this.labelTextColor, this.gitBranchLabel5 = this.gitBranchLabel5 || this.labelTextColor, this.gitBranchLabel6 = this.gitBranchLabel6 || this.labelTextColor, this.gitBranchLabel7 = this.gitBranchLabel7 || this.labelTextColor, this.tagLabelColor = this.tagLabelColor || this.primaryTextColor, this.tagLabelBackground = this.tagLabelBackground || this.primaryColor, this.tagLabelBorder = this.tagBorder || this.primaryBorderColor, this.tagLabelFontSize = this.tagLabelFontSize || "10px", this.commitLabelColor = this.commitLabelColor || this.secondaryTextColor, this.commitLabelBackground = this.commitLabelBackground || this.secondaryColor, this.commitLabelFontSize = this.commitLabelFontSize || "10px", this.attributeBackgroundColorOdd = this.attributeBackgroundColorOdd || w(this.background, 12), this.attributeBackgroundColorEven = this.attributeBackgroundColorEven || w(this.background, 2);
  }
  calculate(e) {
    if (typeof e != "object") {
      this.updateColors();
      return;
    }
    const i = Object.keys(e);
    i.forEach((r) => {
      this[r] = e[r];
    }), this.updateColors(), i.forEach((r) => {
      this[r] = e[r];
    });
  }
};
const Ip = (t) => {
  const e = new Mp();
  return e.calculate(t), e;
};
let $p = class {
  constructor() {
    this.background = "#f4f4f4", this.primaryColor = "#ECECFF", this.secondaryColor = g(this.primaryColor, { h: 120 }), this.secondaryColor = "#ffffde", this.tertiaryColor = g(this.primaryColor, { h: -160 }), this.primaryBorderColor = dt(this.primaryColor, this.darkMode), this.secondaryBorderColor = dt(this.secondaryColor, this.darkMode), this.tertiaryBorderColor = dt(this.tertiaryColor, this.darkMode), this.primaryTextColor = _(this.primaryColor), this.secondaryTextColor = _(this.secondaryColor), this.tertiaryTextColor = _(this.tertiaryColor), this.lineColor = _(this.background), this.textColor = _(this.background), this.background = "white", this.mainBkg = "#ECECFF", this.secondBkg = "#ffffde", this.lineColor = "#333333", this.border1 = "#9370DB", this.border2 = "#aaaa33", this.arrowheadColor = "#333333", this.fontFamily = '"trebuchet ms", verdana, arial, sans-serif', this.fontSize = "16px", this.labelBackground = "#e8e8e8", this.textColor = "#333", this.THEME_COLOR_LIMIT = 12, this.nodeBkg = "calculated", this.nodeBorder = "calculated", this.clusterBkg = "calculated", this.clusterBorder = "calculated", this.defaultLinkColor = "calculated", this.titleColor = "calculated", this.edgeLabelBackground = "calculated", this.actorBorder = "calculated", this.actorBkg = "calculated", this.actorTextColor = "black", this.actorLineColor = "grey", this.signalColor = "calculated", this.signalTextColor = "calculated", this.labelBoxBkgColor = "calculated", this.labelBoxBorderColor = "calculated", this.labelTextColor = "calculated", this.loopTextColor = "calculated", this.noteBorderColor = "calculated", this.noteBkgColor = "#fff5ad", this.noteTextColor = "calculated", this.activationBorderColor = "#666", this.activationBkgColor = "#f4f4f4", this.sequenceNumberColor = "white", this.sectionBkgColor = "calculated", this.altSectionBkgColor = "calculated", this.sectionBkgColor2 = "calculated", this.excludeBkgColor = "#eeeeee", this.taskBorderColor = "calculated", this.taskBkgColor = "calculated", this.taskTextLightColor = "calculated", this.taskTextColor = this.taskTextLightColor, this.taskTextDarkColor = "calculated", this.taskTextOutsideColor = this.taskTextDarkColor, this.taskTextClickableColor = "calculated", this.activeTaskBorderColor = "calculated", this.activeTaskBkgColor = "calculated", this.gridColor = "calculated", this.doneTaskBkgColor = "calculated", this.doneTaskBorderColor = "calculated", this.critBorderColor = "calculated", this.critBkgColor = "calculated", this.todayLineColor = "calculated", this.sectionBkgColor = li(102, 102, 255, 0.49), this.altSectionBkgColor = "white", this.sectionBkgColor2 = "#fff400", this.taskBorderColor = "#534fbc", this.taskBkgColor = "#8a90dd", this.taskTextLightColor = "white", this.taskTextColor = "calculated", this.taskTextDarkColor = "black", this.taskTextOutsideColor = "calculated", this.taskTextClickableColor = "#003163", this.activeTaskBorderColor = "#534fbc", this.activeTaskBkgColor = "#bfc7ff", this.gridColor = "lightgrey", this.doneTaskBkgColor = "lightgrey", this.doneTaskBorderColor = "grey", this.critBorderColor = "#ff8888", this.critBkgColor = "red", this.todayLineColor = "red", this.personBorder = this.primaryBorderColor, this.personBkg = this.mainBkg, this.labelColor = "black", this.errorBkgColor = "#552222", this.errorTextColor = "#552222", this.updateColors();
  }
  updateColors() {
    var e, i, r, n, o, s, a, l, h, u, d;
    this.cScale0 = this.cScale0 || this.primaryColor, this.cScale1 = this.cScale1 || this.secondaryColor, this.cScale2 = this.cScale2 || this.tertiaryColor, this.cScale3 = this.cScale3 || g(this.primaryColor, { h: 30 }), this.cScale4 = this.cScale4 || g(this.primaryColor, { h: 60 }), this.cScale5 = this.cScale5 || g(this.primaryColor, { h: 90 }), this.cScale6 = this.cScale6 || g(this.primaryColor, { h: 120 }), this.cScale7 = this.cScale7 || g(this.primaryColor, { h: 150 }), this.cScale8 = this.cScale8 || g(this.primaryColor, { h: 210 }), this.cScale9 = this.cScale9 || g(this.primaryColor, { h: 270 }), this.cScale10 = this.cScale10 || g(this.primaryColor, { h: 300 }), this.cScale11 = this.cScale11 || g(this.primaryColor, { h: 330 }), this["cScalePeer1"] = this["cScalePeer1"] || O(this.secondaryColor, 45), this["cScalePeer2"] = this["cScalePeer2"] || O(this.tertiaryColor, 40);
    for (let c = 0; c < this.THEME_COLOR_LIMIT; c++)
      this["cScale" + c] = O(this["cScale" + c], 10), this["cScalePeer" + c] = this["cScalePeer" + c] || O(this["cScale" + c], 25);
    for (let c = 0; c < this.THEME_COLOR_LIMIT; c++)
      this["cScaleInv" + c] = this["cScaleInv" + c] || g(this["cScale" + c], { h: 180 });
    for (let c = 0; c < 5; c++)
      this["surface" + c] = this["surface" + c] || g(this.mainBkg, { h: 30, l: -(5 + c * 5) }), this["surfacePeer" + c] = this["surfacePeer" + c] || g(this.mainBkg, { h: 30, l: -(7 + c * 5) });
    if (this.scaleLabelColor = this.scaleLabelColor !== "calculated" && this.scaleLabelColor ? this.scaleLabelColor : this.labelTextColor, this.labelTextColor !== "calculated") {
      this.cScaleLabel0 = this.cScaleLabel0 || _(this.labelTextColor), this.cScaleLabel3 = this.cScaleLabel3 || _(this.labelTextColor);
      for (let c = 0; c < this.THEME_COLOR_LIMIT; c++)
        this["cScaleLabel" + c] = this["cScaleLabel" + c] || this.labelTextColor;
    }
    this.nodeBkg = this.mainBkg, this.nodeBorder = this.border1, this.clusterBkg = this.secondBkg, this.clusterBorder = this.border2, this.defaultLinkColor = this.lineColor, this.titleColor = this.textColor, this.edgeLabelBackground = this.labelBackground, this.actorBorder = w(this.border1, 23), this.actorBkg = this.mainBkg, this.labelBoxBkgColor = this.actorBkg, this.signalColor = this.textColor, this.signalTextColor = this.textColor, this.labelBoxBorderColor = this.actorBorder, this.labelTextColor = this.actorTextColor, this.loopTextColor = this.actorTextColor, this.noteBorderColor = this.border2, this.noteTextColor = this.actorTextColor, this.taskTextColor = this.taskTextLightColor, this.taskTextOutsideColor = this.taskTextDarkColor, this.transitionColor = this.transitionColor || this.lineColor, this.transitionLabelColor = this.transitionLabelColor || this.textColor, this.stateLabelColor = this.stateLabelColor || this.stateBkg || this.primaryTextColor, this.stateBkg = this.stateBkg || this.mainBkg, this.labelBackgroundColor = this.labelBackgroundColor || this.stateBkg, this.compositeBackground = this.compositeBackground || this.background || this.tertiaryColor, this.altBackground = this.altBackground || "#f0f0f0", this.compositeTitleBackground = this.compositeTitleBackground || this.mainBkg, this.compositeBorder = this.compositeBorder || this.nodeBorder, this.innerEndBackground = this.nodeBorder, this.specialStateColor = this.lineColor, this.errorBkgColor = this.errorBkgColor || this.tertiaryColor, this.errorTextColor = this.errorTextColor || this.tertiaryTextColor, this.transitionColor = this.transitionColor || this.lineColor, this.classText = this.primaryTextColor, this.fillType0 = this.primaryColor, this.fillType1 = this.secondaryColor, this.fillType2 = g(this.primaryColor, { h: 64 }), this.fillType3 = g(this.secondaryColor, { h: 64 }), this.fillType4 = g(this.primaryColor, { h: -64 }), this.fillType5 = g(this.secondaryColor, { h: -64 }), this.fillType6 = g(this.primaryColor, { h: 128 }), this.fillType7 = g(this.secondaryColor, { h: 128 }), this.pie1 = this.pie1 || this.primaryColor, this.pie2 = this.pie2 || this.secondaryColor, this.pie3 = this.pie3 || g(this.tertiaryColor, { l: -40 }), this.pie4 = this.pie4 || g(this.primaryColor, { l: -10 }), this.pie5 = this.pie5 || g(this.secondaryColor, { l: -30 }), this.pie6 = this.pie6 || g(this.tertiaryColor, { l: -20 }), this.pie7 = this.pie7 || g(this.primaryColor, { h: 60, l: -20 }), this.pie8 = this.pie8 || g(this.primaryColor, { h: -60, l: -40 }), this.pie9 = this.pie9 || g(this.primaryColor, { h: 120, l: -40 }), this.pie10 = this.pie10 || g(this.primaryColor, { h: 60, l: -40 }), this.pie11 = this.pie11 || g(this.primaryColor, { h: -90, l: -40 }), this.pie12 = this.pie12 || g(this.primaryColor, { h: 120, l: -30 }), this.pieTitleTextSize = this.pieTitleTextSize || "25px", this.pieTitleTextColor = this.pieTitleTextColor || this.taskTextDarkColor, this.pieSectionTextSize = this.pieSectionTextSize || "17px", this.pieSectionTextColor = this.pieSectionTextColor || this.textColor, this.pieLegendTextSize = this.pieLegendTextSize || "17px", this.pieLegendTextColor = this.pieLegendTextColor || this.taskTextDarkColor, this.pieStrokeColor = this.pieStrokeColor || "black", this.pieStrokeWidth = this.pieStrokeWidth || "2px", this.pieOuterStrokeWidth = this.pieOuterStrokeWidth || "2px", this.pieOuterStrokeColor = this.pieOuterStrokeColor || "black", this.pieOpacity = this.pieOpacity || "0.7", this.quadrant1Fill = this.quadrant1Fill || this.primaryColor, this.quadrant2Fill = this.quadrant2Fill || g(this.primaryColor, { r: 5, g: 5, b: 5 }), this.quadrant3Fill = this.quadrant3Fill || g(this.primaryColor, { r: 10, g: 10, b: 10 }), this.quadrant4Fill = this.quadrant4Fill || g(this.primaryColor, { r: 15, g: 15, b: 15 }), this.quadrant1TextFill = this.quadrant1TextFill || this.primaryTextColor, this.quadrant2TextFill = this.quadrant2TextFill || g(this.primaryTextColor, { r: -5, g: -5, b: -5 }), this.quadrant3TextFill = this.quadrant3TextFill || g(this.primaryTextColor, { r: -10, g: -10, b: -10 }), this.quadrant4TextFill = this.quadrant4TextFill || g(this.primaryTextColor, { r: -15, g: -15, b: -15 }), this.quadrantPointFill = this.quadrantPointFill || vi(this.quadrant1Fill) ? w(this.quadrant1Fill) : O(this.quadrant1Fill), this.quadrantPointTextFill = this.quadrantPointTextFill || this.primaryTextColor, this.quadrantXAxisTextFill = this.quadrantXAxisTextFill || this.primaryTextColor, this.quadrantYAxisTextFill = this.quadrantYAxisTextFill || this.primaryTextColor, this.quadrantInternalBorderStrokeFill = this.quadrantInternalBorderStrokeFill || this.primaryBorderColor, this.quadrantExternalBorderStrokeFill = this.quadrantExternalBorderStrokeFill || this.primaryBorderColor, this.quadrantTitleFill = this.quadrantTitleFill || this.primaryTextColor, this.xyChart = {
      backgroundColor: ((e = this.xyChart) == null ? void 0 : e.backgroundColor) || this.background,
      titleColor: ((i = this.xyChart) == null ? void 0 : i.titleColor) || this.primaryTextColor,
      xAxisTitleColor: ((r = this.xyChart) == null ? void 0 : r.xAxisTitleColor) || this.primaryTextColor,
      xAxisLabelColor: ((n = this.xyChart) == null ? void 0 : n.xAxisLabelColor) || this.primaryTextColor,
      xAxisTickColor: ((o = this.xyChart) == null ? void 0 : o.xAxisTickColor) || this.primaryTextColor,
      xAxisLineColor: ((s = this.xyChart) == null ? void 0 : s.xAxisLineColor) || this.primaryTextColor,
      yAxisTitleColor: ((a = this.xyChart) == null ? void 0 : a.yAxisTitleColor) || this.primaryTextColor,
      yAxisLabelColor: ((l = this.xyChart) == null ? void 0 : l.yAxisLabelColor) || this.primaryTextColor,
      yAxisTickColor: ((h = this.xyChart) == null ? void 0 : h.yAxisTickColor) || this.primaryTextColor,
      yAxisLineColor: ((u = this.xyChart) == null ? void 0 : u.yAxisLineColor) || this.primaryTextColor,
      plotColorPalette: ((d = this.xyChart) == null ? void 0 : d.plotColorPalette) || "#ECECFF,#8493A6,#FFC3A0,#DCDDE1,#B8E994,#D1A36F,#C3CDE6,#FFB6C1,#496078,#F8F3E3"
    }, this.requirementBackground = this.requirementBackground || this.primaryColor, this.requirementBorderColor = this.requirementBorderColor || this.primaryBorderColor, this.requirementBorderSize = this.requirementBorderSize || "1", this.requirementTextColor = this.requirementTextColor || this.primaryTextColor, this.relationColor = this.relationColor || this.lineColor, this.relationLabelBackground = this.relationLabelBackground || this.labelBackground, this.relationLabelColor = this.relationLabelColor || this.actorTextColor, this.git0 = this.git0 || this.primaryColor, this.git1 = this.git1 || this.secondaryColor, this.git2 = this.git2 || this.tertiaryColor, this.git3 = this.git3 || g(this.primaryColor, { h: -30 }), this.git4 = this.git4 || g(this.primaryColor, { h: -60 }), this.git5 = this.git5 || g(this.primaryColor, { h: -90 }), this.git6 = this.git6 || g(this.primaryColor, { h: 60 }), this.git7 = this.git7 || g(this.primaryColor, { h: 120 }), this.darkMode ? (this.git0 = w(this.git0, 25), this.git1 = w(this.git1, 25), this.git2 = w(this.git2, 25), this.git3 = w(this.git3, 25), this.git4 = w(this.git4, 25), this.git5 = w(this.git5, 25), this.git6 = w(this.git6, 25), this.git7 = w(this.git7, 25)) : (this.git0 = O(this.git0, 25), this.git1 = O(this.git1, 25), this.git2 = O(this.git2, 25), this.git3 = O(this.git3, 25), this.git4 = O(this.git4, 25), this.git5 = O(this.git5, 25), this.git6 = O(this.git6, 25), this.git7 = O(this.git7, 25)), this.gitInv0 = this.gitInv0 || O(_(this.git0), 25), this.gitInv1 = this.gitInv1 || _(this.git1), this.gitInv2 = this.gitInv2 || _(this.git2), this.gitInv3 = this.gitInv3 || _(this.git3), this.gitInv4 = this.gitInv4 || _(this.git4), this.gitInv5 = this.gitInv5 || _(this.git5), this.gitInv6 = this.gitInv6 || _(this.git6), this.gitInv7 = this.gitInv7 || _(this.git7), this.gitBranchLabel0 = this.gitBranchLabel0 || _(this.labelTextColor), this.gitBranchLabel1 = this.gitBranchLabel1 || this.labelTextColor, this.gitBranchLabel2 = this.gitBranchLabel2 || this.labelTextColor, this.gitBranchLabel3 = this.gitBranchLabel3 || _(this.labelTextColor), this.gitBranchLabel4 = this.gitBranchLabel4 || this.labelTextColor, this.gitBranchLabel5 = this.gitBranchLabel5 || this.labelTextColor, this.gitBranchLabel6 = this.gitBranchLabel6 || this.labelTextColor, this.gitBranchLabel7 = this.gitBranchLabel7 || this.labelTextColor, this.tagLabelColor = this.tagLabelColor || this.primaryTextColor, this.tagLabelBackground = this.tagLabelBackground || this.primaryColor, this.tagLabelBorder = this.tagBorder || this.primaryBorderColor, this.tagLabelFontSize = this.tagLabelFontSize || "10px", this.commitLabelColor = this.commitLabelColor || this.secondaryTextColor, this.commitLabelBackground = this.commitLabelBackground || this.secondaryColor, this.commitLabelFontSize = this.commitLabelFontSize || "10px", this.attributeBackgroundColorOdd = this.attributeBackgroundColorOdd || vr, this.attributeBackgroundColorEven = this.attributeBackgroundColorEven || wr;
  }
  calculate(e) {
    if (typeof e != "object") {
      this.updateColors();
      return;
    }
    const i = Object.keys(e);
    i.forEach((r) => {
      this[r] = e[r];
    }), this.updateColors(), i.forEach((r) => {
      this[r] = e[r];
    });
  }
};
const Dp = (t) => {
  const e = new $p();
  return e.calculate(t), e;
};
let Np = class {
  constructor() {
    this.background = "#f4f4f4", this.primaryColor = "#cde498", this.secondaryColor = "#cdffb2", this.background = "white", this.mainBkg = "#cde498", this.secondBkg = "#cdffb2", this.lineColor = "green", this.border1 = "#13540c", this.border2 = "#6eaa49", this.arrowheadColor = "green", this.fontFamily = '"trebuchet ms", verdana, arial, sans-serif', this.fontSize = "16px", this.tertiaryColor = w("#cde498", 10), this.primaryBorderColor = dt(this.primaryColor, this.darkMode), this.secondaryBorderColor = dt(this.secondaryColor, this.darkMode), this.tertiaryBorderColor = dt(this.tertiaryColor, this.darkMode), this.primaryTextColor = _(this.primaryColor), this.secondaryTextColor = _(this.secondaryColor), this.tertiaryTextColor = _(this.primaryColor), this.lineColor = _(this.background), this.textColor = _(this.background), this.THEME_COLOR_LIMIT = 12, this.nodeBkg = "calculated", this.nodeBorder = "calculated", this.clusterBkg = "calculated", this.clusterBorder = "calculated", this.defaultLinkColor = "calculated", this.titleColor = "#333", this.edgeLabelBackground = "#e8e8e8", this.actorBorder = "calculated", this.actorBkg = "calculated", this.actorTextColor = "black", this.actorLineColor = "grey", this.signalColor = "#333", this.signalTextColor = "#333", this.labelBoxBkgColor = "calculated", this.labelBoxBorderColor = "#326932", this.labelTextColor = "calculated", this.loopTextColor = "calculated", this.noteBorderColor = "calculated", this.noteBkgColor = "#fff5ad", this.noteTextColor = "calculated", this.activationBorderColor = "#666", this.activationBkgColor = "#f4f4f4", this.sequenceNumberColor = "white", this.sectionBkgColor = "#6eaa49", this.altSectionBkgColor = "white", this.sectionBkgColor2 = "#6eaa49", this.excludeBkgColor = "#eeeeee", this.taskBorderColor = "calculated", this.taskBkgColor = "#487e3a", this.taskTextLightColor = "white", this.taskTextColor = "calculated", this.taskTextDarkColor = "black", this.taskTextOutsideColor = "calculated", this.taskTextClickableColor = "#003163", this.activeTaskBorderColor = "calculated", this.activeTaskBkgColor = "calculated", this.gridColor = "lightgrey", this.doneTaskBkgColor = "lightgrey", this.doneTaskBorderColor = "grey", this.critBorderColor = "#ff8888", this.critBkgColor = "red", this.todayLineColor = "red", this.personBorder = this.primaryBorderColor, this.personBkg = this.mainBkg, this.labelColor = "black", this.errorBkgColor = "#552222", this.errorTextColor = "#552222";
  }
  updateColors() {
    var e, i, r, n, o, s, a, l, h, u, d;
    this.actorBorder = O(this.mainBkg, 20), this.actorBkg = this.mainBkg, this.labelBoxBkgColor = this.actorBkg, this.labelTextColor = this.actorTextColor, this.loopTextColor = this.actorTextColor, this.noteBorderColor = this.border2, this.noteTextColor = this.actorTextColor, this.cScale0 = this.cScale0 || this.primaryColor, this.cScale1 = this.cScale1 || this.secondaryColor, this.cScale2 = this.cScale2 || this.tertiaryColor, this.cScale3 = this.cScale3 || g(this.primaryColor, { h: 30 }), this.cScale4 = this.cScale4 || g(this.primaryColor, { h: 60 }), this.cScale5 = this.cScale5 || g(this.primaryColor, { h: 90 }), this.cScale6 = this.cScale6 || g(this.primaryColor, { h: 120 }), this.cScale7 = this.cScale7 || g(this.primaryColor, { h: 150 }), this.cScale8 = this.cScale8 || g(this.primaryColor, { h: 210 }), this.cScale9 = this.cScale9 || g(this.primaryColor, { h: 270 }), this.cScale10 = this.cScale10 || g(this.primaryColor, { h: 300 }), this.cScale11 = this.cScale11 || g(this.primaryColor, { h: 330 }), this["cScalePeer1"] = this["cScalePeer1"] || O(this.secondaryColor, 45), this["cScalePeer2"] = this["cScalePeer2"] || O(this.tertiaryColor, 40);
    for (let c = 0; c < this.THEME_COLOR_LIMIT; c++)
      this["cScale" + c] = O(this["cScale" + c], 10), this["cScalePeer" + c] = this["cScalePeer" + c] || O(this["cScale" + c], 25);
    for (let c = 0; c < this.THEME_COLOR_LIMIT; c++)
      this["cScaleInv" + c] = this["cScaleInv" + c] || g(this["cScale" + c], { h: 180 });
    this.scaleLabelColor = this.scaleLabelColor !== "calculated" && this.scaleLabelColor ? this.scaleLabelColor : this.labelTextColor;
    for (let c = 0; c < this.THEME_COLOR_LIMIT; c++)
      this["cScaleLabel" + c] = this["cScaleLabel" + c] || this.scaleLabelColor;
    for (let c = 0; c < 5; c++)
      this["surface" + c] = this["surface" + c] || g(this.mainBkg, { h: 30, s: -30, l: -(5 + c * 5) }), this["surfacePeer" + c] = this["surfacePeer" + c] || g(this.mainBkg, { h: 30, s: -30, l: -(8 + c * 5) });
    this.nodeBkg = this.mainBkg, this.nodeBorder = this.border1, this.clusterBkg = this.secondBkg, this.clusterBorder = this.border2, this.defaultLinkColor = this.lineColor, this.taskBorderColor = this.border1, this.taskTextColor = this.taskTextLightColor, this.taskTextOutsideColor = this.taskTextDarkColor, this.activeTaskBorderColor = this.taskBorderColor, this.activeTaskBkgColor = this.mainBkg, this.transitionColor = this.transitionColor || this.lineColor, this.transitionLabelColor = this.transitionLabelColor || this.textColor, this.stateLabelColor = this.stateLabelColor || this.stateBkg || this.primaryTextColor, this.stateBkg = this.stateBkg || this.mainBkg, this.labelBackgroundColor = this.labelBackgroundColor || this.stateBkg, this.compositeBackground = this.compositeBackground || this.background || this.tertiaryColor, this.altBackground = this.altBackground || "#f0f0f0", this.compositeTitleBackground = this.compositeTitleBackground || this.mainBkg, this.compositeBorder = this.compositeBorder || this.nodeBorder, this.innerEndBackground = this.primaryBorderColor, this.specialStateColor = this.lineColor, this.errorBkgColor = this.errorBkgColor || this.tertiaryColor, this.errorTextColor = this.errorTextColor || this.tertiaryTextColor, this.transitionColor = this.transitionColor || this.lineColor, this.classText = this.primaryTextColor, this.fillType0 = this.primaryColor, this.fillType1 = this.secondaryColor, this.fillType2 = g(this.primaryColor, { h: 64 }), this.fillType3 = g(this.secondaryColor, { h: 64 }), this.fillType4 = g(this.primaryColor, { h: -64 }), this.fillType5 = g(this.secondaryColor, { h: -64 }), this.fillType6 = g(this.primaryColor, { h: 128 }), this.fillType7 = g(this.secondaryColor, { h: 128 }), this.pie1 = this.pie1 || this.primaryColor, this.pie2 = this.pie2 || this.secondaryColor, this.pie3 = this.pie3 || this.tertiaryColor, this.pie4 = this.pie4 || g(this.primaryColor, { l: -30 }), this.pie5 = this.pie5 || g(this.secondaryColor, { l: -30 }), this.pie6 = this.pie6 || g(this.tertiaryColor, { h: 40, l: -40 }), this.pie7 = this.pie7 || g(this.primaryColor, { h: 60, l: -10 }), this.pie8 = this.pie8 || g(this.primaryColor, { h: -60, l: -10 }), this.pie9 = this.pie9 || g(this.primaryColor, { h: 120, l: 0 }), this.pie10 = this.pie10 || g(this.primaryColor, { h: 60, l: -50 }), this.pie11 = this.pie11 || g(this.primaryColor, { h: -60, l: -50 }), this.pie12 = this.pie12 || g(this.primaryColor, { h: 120, l: -50 }), this.pieTitleTextSize = this.pieTitleTextSize || "25px", this.pieTitleTextColor = this.pieTitleTextColor || this.taskTextDarkColor, this.pieSectionTextSize = this.pieSectionTextSize || "17px", this.pieSectionTextColor = this.pieSectionTextColor || this.textColor, this.pieLegendTextSize = this.pieLegendTextSize || "17px", this.pieLegendTextColor = this.pieLegendTextColor || this.taskTextDarkColor, this.pieStrokeColor = this.pieStrokeColor || "black", this.pieStrokeWidth = this.pieStrokeWidth || "2px", this.pieOuterStrokeWidth = this.pieOuterStrokeWidth || "2px", this.pieOuterStrokeColor = this.pieOuterStrokeColor || "black", this.pieOpacity = this.pieOpacity || "0.7", this.quadrant1Fill = this.quadrant1Fill || this.primaryColor, this.quadrant2Fill = this.quadrant2Fill || g(this.primaryColor, { r: 5, g: 5, b: 5 }), this.quadrant3Fill = this.quadrant3Fill || g(this.primaryColor, { r: 10, g: 10, b: 10 }), this.quadrant4Fill = this.quadrant4Fill || g(this.primaryColor, { r: 15, g: 15, b: 15 }), this.quadrant1TextFill = this.quadrant1TextFill || this.primaryTextColor, this.quadrant2TextFill = this.quadrant2TextFill || g(this.primaryTextColor, { r: -5, g: -5, b: -5 }), this.quadrant3TextFill = this.quadrant3TextFill || g(this.primaryTextColor, { r: -10, g: -10, b: -10 }), this.quadrant4TextFill = this.quadrant4TextFill || g(this.primaryTextColor, { r: -15, g: -15, b: -15 }), this.quadrantPointFill = this.quadrantPointFill || vi(this.quadrant1Fill) ? w(this.quadrant1Fill) : O(this.quadrant1Fill), this.quadrantPointTextFill = this.quadrantPointTextFill || this.primaryTextColor, this.quadrantXAxisTextFill = this.quadrantXAxisTextFill || this.primaryTextColor, this.quadrantYAxisTextFill = this.quadrantYAxisTextFill || this.primaryTextColor, this.quadrantInternalBorderStrokeFill = this.quadrantInternalBorderStrokeFill || this.primaryBorderColor, this.quadrantExternalBorderStrokeFill = this.quadrantExternalBorderStrokeFill || this.primaryBorderColor, this.quadrantTitleFill = this.quadrantTitleFill || this.primaryTextColor, this.xyChart = {
      backgroundColor: ((e = this.xyChart) == null ? void 0 : e.backgroundColor) || this.background,
      titleColor: ((i = this.xyChart) == null ? void 0 : i.titleColor) || this.primaryTextColor,
      xAxisTitleColor: ((r = this.xyChart) == null ? void 0 : r.xAxisTitleColor) || this.primaryTextColor,
      xAxisLabelColor: ((n = this.xyChart) == null ? void 0 : n.xAxisLabelColor) || this.primaryTextColor,
      xAxisTickColor: ((o = this.xyChart) == null ? void 0 : o.xAxisTickColor) || this.primaryTextColor,
      xAxisLineColor: ((s = this.xyChart) == null ? void 0 : s.xAxisLineColor) || this.primaryTextColor,
      yAxisTitleColor: ((a = this.xyChart) == null ? void 0 : a.yAxisTitleColor) || this.primaryTextColor,
      yAxisLabelColor: ((l = this.xyChart) == null ? void 0 : l.yAxisLabelColor) || this.primaryTextColor,
      yAxisTickColor: ((h = this.xyChart) == null ? void 0 : h.yAxisTickColor) || this.primaryTextColor,
      yAxisLineColor: ((u = this.xyChart) == null ? void 0 : u.yAxisLineColor) || this.primaryTextColor,
      plotColorPalette: ((d = this.xyChart) == null ? void 0 : d.plotColorPalette) || "#CDE498,#FF6B6B,#A0D2DB,#D7BDE2,#F0F0F0,#FFC3A0,#7FD8BE,#FF9A8B,#FAF3E0,#FFF176"
    }, this.requirementBackground = this.requirementBackground || this.primaryColor, this.requirementBorderColor = this.requirementBorderColor || this.primaryBorderColor, this.requirementBorderSize = this.requirementBorderSize || "1", this.requirementTextColor = this.requirementTextColor || this.primaryTextColor, this.relationColor = this.relationColor || this.lineColor, this.relationLabelBackground = this.relationLabelBackground || this.edgeLabelBackground, this.relationLabelColor = this.relationLabelColor || this.actorTextColor, this.git0 = this.git0 || this.primaryColor, this.git1 = this.git1 || this.secondaryColor, this.git2 = this.git2 || this.tertiaryColor, this.git3 = this.git3 || g(this.primaryColor, { h: -30 }), this.git4 = this.git4 || g(this.primaryColor, { h: -60 }), this.git5 = this.git5 || g(this.primaryColor, { h: -90 }), this.git6 = this.git6 || g(this.primaryColor, { h: 60 }), this.git7 = this.git7 || g(this.primaryColor, { h: 120 }), this.darkMode ? (this.git0 = w(this.git0, 25), this.git1 = w(this.git1, 25), this.git2 = w(this.git2, 25), this.git3 = w(this.git3, 25), this.git4 = w(this.git4, 25), this.git5 = w(this.git5, 25), this.git6 = w(this.git6, 25), this.git7 = w(this.git7, 25)) : (this.git0 = O(this.git0, 25), this.git1 = O(this.git1, 25), this.git2 = O(this.git2, 25), this.git3 = O(this.git3, 25), this.git4 = O(this.git4, 25), this.git5 = O(this.git5, 25), this.git6 = O(this.git6, 25), this.git7 = O(this.git7, 25)), this.gitInv0 = this.gitInv0 || _(this.git0), this.gitInv1 = this.gitInv1 || _(this.git1), this.gitInv2 = this.gitInv2 || _(this.git2), this.gitInv3 = this.gitInv3 || _(this.git3), this.gitInv4 = this.gitInv4 || _(this.git4), this.gitInv5 = this.gitInv5 || _(this.git5), this.gitInv6 = this.gitInv6 || _(this.git6), this.gitInv7 = this.gitInv7 || _(this.git7), this.gitBranchLabel0 = this.gitBranchLabel0 || _(this.labelTextColor), this.gitBranchLabel1 = this.gitBranchLabel1 || this.labelTextColor, this.gitBranchLabel2 = this.gitBranchLabel2 || this.labelTextColor, this.gitBranchLabel3 = this.gitBranchLabel3 || _(this.labelTextColor), this.gitBranchLabel4 = this.gitBranchLabel4 || this.labelTextColor, this.gitBranchLabel5 = this.gitBranchLabel5 || this.labelTextColor, this.gitBranchLabel6 = this.gitBranchLabel6 || this.labelTextColor, this.gitBranchLabel7 = this.gitBranchLabel7 || this.labelTextColor, this.tagLabelColor = this.tagLabelColor || this.primaryTextColor, this.tagLabelBackground = this.tagLabelBackground || this.primaryColor, this.tagLabelBorder = this.tagBorder || this.primaryBorderColor, this.tagLabelFontSize = this.tagLabelFontSize || "10px", this.commitLabelColor = this.commitLabelColor || this.secondaryTextColor, this.commitLabelBackground = this.commitLabelBackground || this.secondaryColor, this.commitLabelFontSize = this.commitLabelFontSize || "10px", this.attributeBackgroundColorOdd = this.attributeBackgroundColorOdd || vr, this.attributeBackgroundColorEven = this.attributeBackgroundColorEven || wr;
  }
  calculate(e) {
    if (typeof e != "object") {
      this.updateColors();
      return;
    }
    const i = Object.keys(e);
    i.forEach((r) => {
      this[r] = e[r];
    }), this.updateColors(), i.forEach((r) => {
      this[r] = e[r];
    });
  }
};
const Rp = (t) => {
  const e = new Np();
  return e.calculate(t), e;
};
class Pp {
  constructor() {
    this.primaryColor = "#eee", this.contrast = "#707070", this.secondaryColor = w(this.contrast, 55), this.background = "#ffffff", this.tertiaryColor = g(this.primaryColor, { h: -160 }), this.primaryBorderColor = dt(this.primaryColor, this.darkMode), this.secondaryBorderColor = dt(this.secondaryColor, this.darkMode), this.tertiaryBorderColor = dt(this.tertiaryColor, this.darkMode), this.primaryTextColor = _(this.primaryColor), this.secondaryTextColor = _(this.secondaryColor), this.tertiaryTextColor = _(this.tertiaryColor), this.lineColor = _(this.background), this.textColor = _(this.background), this.mainBkg = "#eee", this.secondBkg = "calculated", this.lineColor = "#666", this.border1 = "#999", this.border2 = "calculated", this.note = "#ffa", this.text = "#333", this.critical = "#d42", this.done = "#bbb", this.arrowheadColor = "#333333", this.fontFamily = '"trebuchet ms", verdana, arial, sans-serif', this.fontSize = "16px", this.THEME_COLOR_LIMIT = 12, this.nodeBkg = "calculated", this.nodeBorder = "calculated", this.clusterBkg = "calculated", this.clusterBorder = "calculated", this.defaultLinkColor = "calculated", this.titleColor = "calculated", this.edgeLabelBackground = "white", this.actorBorder = "calculated", this.actorBkg = "calculated", this.actorTextColor = "calculated", this.actorLineColor = "calculated", this.signalColor = "calculated", this.signalTextColor = "calculated", this.labelBoxBkgColor = "calculated", this.labelBoxBorderColor = "calculated", this.labelTextColor = "calculated", this.loopTextColor = "calculated", this.noteBorderColor = "calculated", this.noteBkgColor = "calculated", this.noteTextColor = "calculated", this.activationBorderColor = "#666", this.activationBkgColor = "#f4f4f4", this.sequenceNumberColor = "white", this.sectionBkgColor = "calculated", this.altSectionBkgColor = "white", this.sectionBkgColor2 = "calculated", this.excludeBkgColor = "#eeeeee", this.taskBorderColor = "calculated", this.taskBkgColor = "calculated", this.taskTextLightColor = "white", this.taskTextColor = "calculated", this.taskTextDarkColor = "calculated", this.taskTextOutsideColor = "calculated", this.taskTextClickableColor = "#003163", this.activeTaskBorderColor = "calculated", this.activeTaskBkgColor = "calculated", this.gridColor = "calculated", this.doneTaskBkgColor = "calculated", this.doneTaskBorderColor = "calculated", this.critBkgColor = "calculated", this.critBorderColor = "calculated", this.todayLineColor = "calculated", this.personBorder = this.primaryBorderColor, this.personBkg = this.mainBkg, this.labelColor = "black", this.errorBkgColor = "#552222", this.errorTextColor = "#552222";
  }
  updateColors() {
    var e, i, r, n, o, s, a, l, h, u, d;
    this.secondBkg = w(this.contrast, 55), this.border2 = this.contrast, this.actorBorder = w(this.border1, 23), this.actorBkg = this.mainBkg, this.actorTextColor = this.text, this.actorLineColor = this.lineColor, this.signalColor = this.text, this.signalTextColor = this.text, this.labelBoxBkgColor = this.actorBkg, this.labelBoxBorderColor = this.actorBorder, this.labelTextColor = this.text, this.loopTextColor = this.text, this.noteBorderColor = "#999", this.noteBkgColor = "#666", this.noteTextColor = "#fff", this.cScale0 = this.cScale0 || "#555", this.cScale1 = this.cScale1 || "#F4F4F4", this.cScale2 = this.cScale2 || "#555", this.cScale3 = this.cScale3 || "#BBB", this.cScale4 = this.cScale4 || "#777", this.cScale5 = this.cScale5 || "#999", this.cScale6 = this.cScale6 || "#DDD", this.cScale7 = this.cScale7 || "#FFF", this.cScale8 = this.cScale8 || "#DDD", this.cScale9 = this.cScale9 || "#BBB", this.cScale10 = this.cScale10 || "#999", this.cScale11 = this.cScale11 || "#777";
    for (let c = 0; c < this.THEME_COLOR_LIMIT; c++)
      this["cScaleInv" + c] = this["cScaleInv" + c] || _(this["cScale" + c]);
    for (let c = 0; c < this.THEME_COLOR_LIMIT; c++)
      this.darkMode ? this["cScalePeer" + c] = this["cScalePeer" + c] || w(this["cScale" + c], 10) : this["cScalePeer" + c] = this["cScalePeer" + c] || O(this["cScale" + c], 10);
    this.scaleLabelColor = this.scaleLabelColor || (this.darkMode ? "black" : this.labelTextColor), this.cScaleLabel0 = this.cScaleLabel0 || this.cScale1, this.cScaleLabel2 = this.cScaleLabel2 || this.cScale1;
    for (let c = 0; c < this.THEME_COLOR_LIMIT; c++)
      this["cScaleLabel" + c] = this["cScaleLabel" + c] || this.scaleLabelColor;
    for (let c = 0; c < 5; c++)
      this["surface" + c] = this["surface" + c] || g(this.mainBkg, { l: -(5 + c * 5) }), this["surfacePeer" + c] = this["surfacePeer" + c] || g(this.mainBkg, { l: -(8 + c * 5) });
    this.nodeBkg = this.mainBkg, this.nodeBorder = this.border1, this.clusterBkg = this.secondBkg, this.clusterBorder = this.border2, this.defaultLinkColor = this.lineColor, this.titleColor = this.text, this.sectionBkgColor = w(this.contrast, 30), this.sectionBkgColor2 = w(this.contrast, 30), this.taskBorderColor = O(this.contrast, 10), this.taskBkgColor = this.contrast, this.taskTextColor = this.taskTextLightColor, this.taskTextDarkColor = this.text, this.taskTextOutsideColor = this.taskTextDarkColor, this.activeTaskBorderColor = this.taskBorderColor, this.activeTaskBkgColor = this.mainBkg, this.gridColor = w(this.border1, 30), this.doneTaskBkgColor = this.done, this.doneTaskBorderColor = this.lineColor, this.critBkgColor = this.critical, this.critBorderColor = O(this.critBkgColor, 10), this.todayLineColor = this.critBkgColor, this.transitionColor = this.transitionColor || "#000", this.transitionLabelColor = this.transitionLabelColor || this.textColor, this.stateLabelColor = this.stateLabelColor || this.stateBkg || this.primaryTextColor, this.stateBkg = this.stateBkg || this.mainBkg, this.labelBackgroundColor = this.labelBackgroundColor || this.stateBkg, this.compositeBackground = this.compositeBackground || this.background || this.tertiaryColor, this.altBackground = this.altBackground || "#f4f4f4", this.compositeTitleBackground = this.compositeTitleBackground || this.mainBkg, this.stateBorder = this.stateBorder || "#000", this.innerEndBackground = this.primaryBorderColor, this.specialStateColor = "#222", this.errorBkgColor = this.errorBkgColor || this.tertiaryColor, this.errorTextColor = this.errorTextColor || this.tertiaryTextColor, this.classText = this.primaryTextColor, this.fillType0 = this.primaryColor, this.fillType1 = this.secondaryColor, this.fillType2 = g(this.primaryColor, { h: 64 }), this.fillType3 = g(this.secondaryColor, { h: 64 }), this.fillType4 = g(this.primaryColor, { h: -64 }), this.fillType5 = g(this.secondaryColor, { h: -64 }), this.fillType6 = g(this.primaryColor, { h: 128 }), this.fillType7 = g(this.secondaryColor, { h: 128 });
    for (let c = 0; c < this.THEME_COLOR_LIMIT; c++)
      this["pie" + c] = this["cScale" + c];
    this.pie12 = this.pie0, this.pieTitleTextSize = this.pieTitleTextSize || "25px", this.pieTitleTextColor = this.pieTitleTextColor || this.taskTextDarkColor, this.pieSectionTextSize = this.pieSectionTextSize || "17px", this.pieSectionTextColor = this.pieSectionTextColor || this.textColor, this.pieLegendTextSize = this.pieLegendTextSize || "17px", this.pieLegendTextColor = this.pieLegendTextColor || this.taskTextDarkColor, this.pieStrokeColor = this.pieStrokeColor || "black", this.pieStrokeWidth = this.pieStrokeWidth || "2px", this.pieOuterStrokeWidth = this.pieOuterStrokeWidth || "2px", this.pieOuterStrokeColor = this.pieOuterStrokeColor || "black", this.pieOpacity = this.pieOpacity || "0.7", this.quadrant1Fill = this.quadrant1Fill || this.primaryColor, this.quadrant2Fill = this.quadrant2Fill || g(this.primaryColor, { r: 5, g: 5, b: 5 }), this.quadrant3Fill = this.quadrant3Fill || g(this.primaryColor, { r: 10, g: 10, b: 10 }), this.quadrant4Fill = this.quadrant4Fill || g(this.primaryColor, { r: 15, g: 15, b: 15 }), this.quadrant1TextFill = this.quadrant1TextFill || this.primaryTextColor, this.quadrant2TextFill = this.quadrant2TextFill || g(this.primaryTextColor, { r: -5, g: -5, b: -5 }), this.quadrant3TextFill = this.quadrant3TextFill || g(this.primaryTextColor, { r: -10, g: -10, b: -10 }), this.quadrant4TextFill = this.quadrant4TextFill || g(this.primaryTextColor, { r: -15, g: -15, b: -15 }), this.quadrantPointFill = this.quadrantPointFill || vi(this.quadrant1Fill) ? w(this.quadrant1Fill) : O(this.quadrant1Fill), this.quadrantPointTextFill = this.quadrantPointTextFill || this.primaryTextColor, this.quadrantXAxisTextFill = this.quadrantXAxisTextFill || this.primaryTextColor, this.quadrantYAxisTextFill = this.quadrantYAxisTextFill || this.primaryTextColor, this.quadrantInternalBorderStrokeFill = this.quadrantInternalBorderStrokeFill || this.primaryBorderColor, this.quadrantExternalBorderStrokeFill = this.quadrantExternalBorderStrokeFill || this.primaryBorderColor, this.quadrantTitleFill = this.quadrantTitleFill || this.primaryTextColor, this.xyChart = {
      backgroundColor: ((e = this.xyChart) == null ? void 0 : e.backgroundColor) || this.background,
      titleColor: ((i = this.xyChart) == null ? void 0 : i.titleColor) || this.primaryTextColor,
      xAxisTitleColor: ((r = this.xyChart) == null ? void 0 : r.xAxisTitleColor) || this.primaryTextColor,
      xAxisLabelColor: ((n = this.xyChart) == null ? void 0 : n.xAxisLabelColor) || this.primaryTextColor,
      xAxisTickColor: ((o = this.xyChart) == null ? void 0 : o.xAxisTickColor) || this.primaryTextColor,
      xAxisLineColor: ((s = this.xyChart) == null ? void 0 : s.xAxisLineColor) || this.primaryTextColor,
      yAxisTitleColor: ((a = this.xyChart) == null ? void 0 : a.yAxisTitleColor) || this.primaryTextColor,
      yAxisLabelColor: ((l = this.xyChart) == null ? void 0 : l.yAxisLabelColor) || this.primaryTextColor,
      yAxisTickColor: ((h = this.xyChart) == null ? void 0 : h.yAxisTickColor) || this.primaryTextColor,
      yAxisLineColor: ((u = this.xyChart) == null ? void 0 : u.yAxisLineColor) || this.primaryTextColor,
      plotColorPalette: ((d = this.xyChart) == null ? void 0 : d.plotColorPalette) || "#EEE,#6BB8E4,#8ACB88,#C7ACD6,#E8DCC2,#FFB2A8,#FFF380,#7E8D91,#FFD8B1,#FAF3E0"
    }, this.requirementBackground = this.requirementBackground || this.primaryColor, this.requirementBorderColor = this.requirementBorderColor || this.primaryBorderColor, this.requirementBorderSize = this.requirementBorderSize || "1", this.requirementTextColor = this.requirementTextColor || this.primaryTextColor, this.relationColor = this.relationColor || this.lineColor, this.relationLabelBackground = this.relationLabelBackground || this.edgeLabelBackground, this.relationLabelColor = this.relationLabelColor || this.actorTextColor, this.git0 = O(this.pie1, 25) || this.primaryColor, this.git1 = this.pie2 || this.secondaryColor, this.git2 = this.pie3 || this.tertiaryColor, this.git3 = this.pie4 || g(this.primaryColor, { h: -30 }), this.git4 = this.pie5 || g(this.primaryColor, { h: -60 }), this.git5 = this.pie6 || g(this.primaryColor, { h: -90 }), this.git6 = this.pie7 || g(this.primaryColor, { h: 60 }), this.git7 = this.pie8 || g(this.primaryColor, { h: 120 }), this.gitInv0 = this.gitInv0 || _(this.git0), this.gitInv1 = this.gitInv1 || _(this.git1), this.gitInv2 = this.gitInv2 || _(this.git2), this.gitInv3 = this.gitInv3 || _(this.git3), this.gitInv4 = this.gitInv4 || _(this.git4), this.gitInv5 = this.gitInv5 || _(this.git5), this.gitInv6 = this.gitInv6 || _(this.git6), this.gitInv7 = this.gitInv7 || _(this.git7), this.branchLabelColor = this.branchLabelColor || this.labelTextColor, this.gitBranchLabel0 = this.branchLabelColor, this.gitBranchLabel1 = "white", this.gitBranchLabel2 = this.branchLabelColor, this.gitBranchLabel3 = "white", this.gitBranchLabel4 = this.branchLabelColor, this.gitBranchLabel5 = this.branchLabelColor, this.gitBranchLabel6 = this.branchLabelColor, this.gitBranchLabel7 = this.branchLabelColor, this.tagLabelColor = this.tagLabelColor || this.primaryTextColor, this.tagLabelBackground = this.tagLabelBackground || this.primaryColor, this.tagLabelBorder = this.tagBorder || this.primaryBorderColor, this.tagLabelFontSize = this.tagLabelFontSize || "10px", this.commitLabelColor = this.commitLabelColor || this.secondaryTextColor, this.commitLabelBackground = this.commitLabelBackground || this.secondaryColor, this.commitLabelFontSize = this.commitLabelFontSize || "10px", this.attributeBackgroundColorOdd = this.attributeBackgroundColorOdd || vr, this.attributeBackgroundColorEven = this.attributeBackgroundColorEven || wr;
  }
  calculate(e) {
    if (typeof e != "object") {
      this.updateColors();
      return;
    }
    const i = Object.keys(e);
    i.forEach((r) => {
      this[r] = e[r];
    }), this.updateColors(), i.forEach((r) => {
      this[r] = e[r];
    });
  }
}
const qp = (t) => {
  const e = new Pp();
  return e.calculate(t), e;
}, te = {
  base: {
    getThemeVariables: Op
  },
  dark: {
    getThemeVariables: Ip
  },
  default: {
    getThemeVariables: Dp
  },
  forest: {
    getThemeVariables: Rp
  },
  neutral: {
    getThemeVariables: qp
  }
}, Zt = {
  flowchart: {
    useMaxWidth: !0,
    titleTopMargin: 25,
    subGraphTitleMargin: {
      top: 0,
      bottom: 0
    },
    diagramPadding: 8,
    htmlLabels: !0,
    nodeSpacing: 50,
    rankSpacing: 50,
    curve: "basis",
    padding: 15,
    defaultRenderer: "dagre-wrapper",
    wrappingWidth: 200
  },
  sequence: {
    useMaxWidth: !0,
    hideUnusedParticipants: !1,
    activationWidth: 10,
    diagramMarginX: 50,
    diagramMarginY: 10,
    actorMargin: 50,
    width: 150,
    height: 65,
    boxMargin: 10,
    boxTextMargin: 5,
    noteMargin: 10,
    messageMargin: 35,
    messageAlign: "center",
    mirrorActors: !0,
    forceMenus: !1,
    bottomMarginAdj: 1,
    rightAngles: !1,
    showSequenceNumbers: !1,
    actorFontSize: 14,
    actorFontFamily: '"Open Sans", sans-serif',
    actorFontWeight: 400,
    noteFontSize: 14,
    noteFontFamily: '"trebuchet ms", verdana, arial, sans-serif',
    noteFontWeight: 400,
    noteAlign: "center",
    messageFontSize: 16,
    messageFontFamily: '"trebuchet ms", verdana, arial, sans-serif',
    messageFontWeight: 400,
    wrap: !1,
    wrapPadding: 10,
    labelBoxWidth: 50,
    labelBoxHeight: 20
  },
  gantt: {
    useMaxWidth: !0,
    titleTopMargin: 25,
    barHeight: 20,
    barGap: 4,
    topPadding: 50,
    rightPadding: 75,
    leftPadding: 75,
    gridLineStartPadding: 35,
    fontSize: 11,
    sectionFontSize: 11,
    numberSectionStyles: 4,
    axisFormat: "%Y-%m-%d",
    topAxis: !1,
    displayMode: "",
    weekday: "sunday"
  },
  journey: {
    useMaxWidth: !0,
    diagramMarginX: 50,
    diagramMarginY: 10,
    leftMargin: 150,
    width: 150,
    height: 50,
    boxMargin: 10,
    boxTextMargin: 5,
    noteMargin: 10,
    messageMargin: 35,
    messageAlign: "center",
    bottomMarginAdj: 1,
    rightAngles: !1,
    taskFontSize: 14,
    taskFontFamily: '"Open Sans", sans-serif',
    taskMargin: 50,
    activationWidth: 10,
    textPlacement: "fo",
    actorColours: [
      "#8FBC8F",
      "#7CFC00",
      "#00FFFF",
      "#20B2AA",
      "#B0E0E6",
      "#FFFFE0"
    ],
    sectionFills: [
      "#191970",
      "#8B008B",
      "#4B0082",
      "#2F4F4F",
      "#800000",
      "#8B4513",
      "#00008B"
    ],
    sectionColours: [
      "#fff"
    ]
  },
  class: {
    useMaxWidth: !0,
    titleTopMargin: 25,
    arrowMarkerAbsolute: !1,
    dividerMargin: 10,
    padding: 5,
    textHeight: 10,
    defaultRenderer: "dagre-wrapper",
    htmlLabels: !1
  },
  state: {
    useMaxWidth: !0,
    titleTopMargin: 25,
    dividerMargin: 10,
    sizeUnit: 5,
    padding: 8,
    textHeight: 10,
    titleShift: -15,
    noteMargin: 10,
    forkWidth: 70,
    forkHeight: 7,
    miniPadding: 2,
    fontSizeFactor: 5.02,
    fontSize: 24,
    labelHeight: 16,
    edgeLengthFactor: "20",
    compositTitleSize: 35,
    radius: 5,
    defaultRenderer: "dagre-wrapper"
  },
  er: {
    useMaxWidth: !0,
    titleTopMargin: 25,
    diagramPadding: 20,
    layoutDirection: "TB",
    minEntityWidth: 100,
    minEntityHeight: 75,
    entityPadding: 15,
    stroke: "gray",
    fill: "honeydew",
    fontSize: 12
  },
  pie: {
    useMaxWidth: !0,
    textPosition: 0.75
  },
  quadrantChart: {
    useMaxWidth: !0,
    chartWidth: 500,
    chartHeight: 500,
    titleFontSize: 20,
    titlePadding: 10,
    quadrantPadding: 5,
    xAxisLabelPadding: 5,
    yAxisLabelPadding: 5,
    xAxisLabelFontSize: 16,
    yAxisLabelFontSize: 16,
    quadrantLabelFontSize: 16,
    quadrantTextTopPadding: 5,
    pointTextPadding: 5,
    pointLabelFontSize: 12,
    pointRadius: 5,
    xAxisPosition: "top",
    yAxisPosition: "left",
    quadrantInternalBorderStrokeWidth: 1,
    quadrantExternalBorderStrokeWidth: 2
  },
  xyChart: {
    useMaxWidth: !0,
    width: 700,
    height: 500,
    titleFontSize: 20,
    titlePadding: 10,
    showTitle: !0,
    xAxis: {
      $ref: "#/$defs/XYChartAxisConfig",
      showLabel: !0,
      labelFontSize: 14,
      labelPadding: 5,
      showTitle: !0,
      titleFontSize: 16,
      titlePadding: 5,
      showTick: !0,
      tickLength: 5,
      tickWidth: 2,
      showAxisLine: !0,
      axisLineWidth: 2
    },
    yAxis: {
      $ref: "#/$defs/XYChartAxisConfig",
      showLabel: !0,
      labelFontSize: 14,
      labelPadding: 5,
      showTitle: !0,
      titleFontSize: 16,
      titlePadding: 5,
      showTick: !0,
      tickLength: 5,
      tickWidth: 2,
      showAxisLine: !0,
      axisLineWidth: 2
    },
    chartOrientation: "vertical",
    plotReservedSpacePercent: 50
  },
  requirement: {
    useMaxWidth: !0,
    rect_fill: "#f9f9f9",
    text_color: "#333",
    rect_border_size: "0.5px",
    rect_border_color: "#bbb",
    rect_min_width: 200,
    rect_min_height: 200,
    fontSize: 14,
    rect_padding: 10,
    line_height: 20
  },
  mindmap: {
    useMaxWidth: !0,
    padding: 10,
    maxNodeWidth: 200
  },
  timeline: {
    useMaxWidth: !0,
    diagramMarginX: 50,
    diagramMarginY: 10,
    leftMargin: 150,
    width: 150,
    height: 50,
    boxMargin: 10,
    boxTextMargin: 5,
    noteMargin: 10,
    messageMargin: 35,
    messageAlign: "center",
    bottomMarginAdj: 1,
    rightAngles: !1,
    taskFontSize: 14,
    taskFontFamily: '"Open Sans", sans-serif',
    taskMargin: 50,
    activationWidth: 10,
    textPlacement: "fo",
    actorColours: [
      "#8FBC8F",
      "#7CFC00",
      "#00FFFF",
      "#20B2AA",
      "#B0E0E6",
      "#FFFFE0"
    ],
    sectionFills: [
      "#191970",
      "#8B008B",
      "#4B0082",
      "#2F4F4F",
      "#800000",
      "#8B4513",
      "#00008B"
    ],
    sectionColours: [
      "#fff"
    ],
    disableMulticolor: !1
  },
  gitGraph: {
    useMaxWidth: !0,
    titleTopMargin: 25,
    diagramPadding: 8,
    nodeLabel: {
      width: 75,
      height: 100,
      x: -25,
      y: 0
    },
    mainBranchName: "main",
    mainBranchOrder: 0,
    showCommitLabel: !0,
    showBranches: !0,
    rotateCommitLabel: !0,
    parallelCommits: !1,
    arrowMarkerAbsolute: !1
  },
  c4: {
    useMaxWidth: !0,
    diagramMarginX: 50,
    diagramMarginY: 10,
    c4ShapeMargin: 50,
    c4ShapePadding: 20,
    width: 216,
    height: 60,
    boxMargin: 10,
    c4ShapeInRow: 4,
    nextLinePaddingX: 0,
    c4BoundaryInRow: 2,
    personFontSize: 14,
    personFontFamily: '"Open Sans", sans-serif',
    personFontWeight: "normal",
    external_personFontSize: 14,
    external_personFontFamily: '"Open Sans", sans-serif',
    external_personFontWeight: "normal",
    systemFontSize: 14,
    systemFontFamily: '"Open Sans", sans-serif',
    systemFontWeight: "normal",
    external_systemFontSize: 14,
    external_systemFontFamily: '"Open Sans", sans-serif',
    external_systemFontWeight: "normal",
    system_dbFontSize: 14,
    system_dbFontFamily: '"Open Sans", sans-serif',
    system_dbFontWeight: "normal",
    external_system_dbFontSize: 14,
    external_system_dbFontFamily: '"Open Sans", sans-serif',
    external_system_dbFontWeight: "normal",
    system_queueFontSize: 14,
    system_queueFontFamily: '"Open Sans", sans-serif',
    system_queueFontWeight: "normal",
    external_system_queueFontSize: 14,
    external_system_queueFontFamily: '"Open Sans", sans-serif',
    external_system_queueFontWeight: "normal",
    boundaryFontSize: 14,
    boundaryFontFamily: '"Open Sans", sans-serif',
    boundaryFontWeight: "normal",
    messageFontSize: 12,
    messageFontFamily: '"Open Sans", sans-serif',
    messageFontWeight: "normal",
    containerFontSize: 14,
    containerFontFamily: '"Open Sans", sans-serif',
    containerFontWeight: "normal",
    external_containerFontSize: 14,
    external_containerFontFamily: '"Open Sans", sans-serif',
    external_containerFontWeight: "normal",
    container_dbFontSize: 14,
    container_dbFontFamily: '"Open Sans", sans-serif',
    container_dbFontWeight: "normal",
    external_container_dbFontSize: 14,
    external_container_dbFontFamily: '"Open Sans", sans-serif',
    external_container_dbFontWeight: "normal",
    container_queueFontSize: 14,
    container_queueFontFamily: '"Open Sans", sans-serif',
    container_queueFontWeight: "normal",
    external_container_queueFontSize: 14,
    external_container_queueFontFamily: '"Open Sans", sans-serif',
    external_container_queueFontWeight: "normal",
    componentFontSize: 14,
    componentFontFamily: '"Open Sans", sans-serif',
    componentFontWeight: "normal",
    external_componentFontSize: 14,
    external_componentFontFamily: '"Open Sans", sans-serif',
    external_componentFontWeight: "normal",
    component_dbFontSize: 14,
    component_dbFontFamily: '"Open Sans", sans-serif',
    component_dbFontWeight: "normal",
    external_component_dbFontSize: 14,
    external_component_dbFontFamily: '"Open Sans", sans-serif',
    external_component_dbFontWeight: "normal",
    component_queueFontSize: 14,
    component_queueFontFamily: '"Open Sans", sans-serif',
    component_queueFontWeight: "normal",
    external_component_queueFontSize: 14,
    external_component_queueFontFamily: '"Open Sans", sans-serif',
    external_component_queueFontWeight: "normal",
    wrap: !0,
    wrapPadding: 10,
    person_bg_color: "#08427B",
    person_border_color: "#073B6F",
    external_person_bg_color: "#686868",
    external_person_border_color: "#8A8A8A",
    system_bg_color: "#1168BD",
    system_border_color: "#3C7FC0",
    system_db_bg_color: "#1168BD",
    system_db_border_color: "#3C7FC0",
    system_queue_bg_color: "#1168BD",
    system_queue_border_color: "#3C7FC0",
    external_system_bg_color: "#999999",
    external_system_border_color: "#8A8A8A",
    external_system_db_bg_color: "#999999",
    external_system_db_border_color: "#8A8A8A",
    external_system_queue_bg_color: "#999999",
    external_system_queue_border_color: "#8A8A8A",
    container_bg_color: "#438DD5",
    container_border_color: "#3C7FC0",
    container_db_bg_color: "#438DD5",
    container_db_border_color: "#3C7FC0",
    container_queue_bg_color: "#438DD5",
    container_queue_border_color: "#3C7FC0",
    external_container_bg_color: "#B3B3B3",
    external_container_border_color: "#A6A6A6",
    external_container_db_bg_color: "#B3B3B3",
    external_container_db_border_color: "#A6A6A6",
    external_container_queue_bg_color: "#B3B3B3",
    external_container_queue_border_color: "#A6A6A6",
    component_bg_color: "#85BBF0",
    component_border_color: "#78A8D8",
    component_db_bg_color: "#85BBF0",
    component_db_border_color: "#78A8D8",
    component_queue_bg_color: "#85BBF0",
    component_queue_border_color: "#78A8D8",
    external_component_bg_color: "#CCCCCC",
    external_component_border_color: "#BFBFBF",
    external_component_db_bg_color: "#CCCCCC",
    external_component_db_border_color: "#BFBFBF",
    external_component_queue_bg_color: "#CCCCCC",
    external_component_queue_border_color: "#BFBFBF"
  },
  sankey: {
    useMaxWidth: !0,
    width: 600,
    height: 400,
    linkColor: "gradient",
    nodeAlignment: "justify",
    showValues: !0,
    prefix: "",
    suffix: ""
  },
  block: {
    useMaxWidth: !0,
    padding: 8
  },
  theme: "default",
  maxTextSize: 5e4,
  maxEdges: 500,
  darkMode: !1,
  fontFamily: '"trebuchet ms", verdana, arial, sans-serif;',
  logLevel: 5,
  securityLevel: "strict",
  startOnLoad: !0,
  arrowMarkerAbsolute: !1,
  secure: [
    "secure",
    "securityLevel",
    "startOnLoad",
    "maxTextSize",
    "maxEdges"
  ],
  legacyMathML: !1,
  deterministicIds: !1,
  fontSize: 16
}, tl = {
  ...Zt,
  // Set, even though they're `undefined` so that `configKeys` finds these keys
  // TODO: Should we replace these with `null` so that they can go in the JSON Schema?
  deterministicIDSeed: void 0,
  themeCSS: void 0,
  // add non-JSON default config values
  themeVariables: te.default.getThemeVariables(),
  sequence: {
    ...Zt.sequence,
    messageFont: function() {
      return {
        fontFamily: this.messageFontFamily,
        fontSize: this.messageFontSize,
        fontWeight: this.messageFontWeight
      };
    },
    noteFont: function() {
      return {
        fontFamily: this.noteFontFamily,
        fontSize: this.noteFontSize,
        fontWeight: this.noteFontWeight
      };
    },
    actorFont: function() {
      return {
        fontFamily: this.actorFontFamily,
        fontSize: this.actorFontSize,
        fontWeight: this.actorFontWeight
      };
    }
  },
  gantt: {
    ...Zt.gantt,
    tickInterval: void 0,
    useWidth: void 0
    // can probably be removed since `configKeys` already includes this
  },
  c4: {
    ...Zt.c4,
    useWidth: void 0,
    personFont: function() {
      return {
        fontFamily: this.personFontFamily,
        fontSize: this.personFontSize,
        fontWeight: this.personFontWeight
      };
    },
    external_personFont: function() {
      return {
        fontFamily: this.external_personFontFamily,
        fontSize: this.external_personFontSize,
        fontWeight: this.external_personFontWeight
      };
    },
    systemFont: function() {
      return {
        fontFamily: this.systemFontFamily,
        fontSize: this.systemFontSize,
        fontWeight: this.systemFontWeight
      };
    },
    external_systemFont: function() {
      return {
        fontFamily: this.external_systemFontFamily,
        fontSize: this.external_systemFontSize,
        fontWeight: this.external_systemFontWeight
      };
    },
    system_dbFont: function() {
      return {
        fontFamily: this.system_dbFontFamily,
        fontSize: this.system_dbFontSize,
        fontWeight: this.system_dbFontWeight
      };
    },
    external_system_dbFont: function() {
      return {
        fontFamily: this.external_system_dbFontFamily,
        fontSize: this.external_system_dbFontSize,
        fontWeight: this.external_system_dbFontWeight
      };
    },
    system_queueFont: function() {
      return {
        fontFamily: this.system_queueFontFamily,
        fontSize: this.system_queueFontSize,
        fontWeight: this.system_queueFontWeight
      };
    },
    external_system_queueFont: function() {
      return {
        fontFamily: this.external_system_queueFontFamily,
        fontSize: this.external_system_queueFontSize,
        fontWeight: this.external_system_queueFontWeight
      };
    },
    containerFont: function() {
      return {
        fontFamily: this.containerFontFamily,
        fontSize: this.containerFontSize,
        fontWeight: this.containerFontWeight
      };
    },
    external_containerFont: function() {
      return {
        fontFamily: this.external_containerFontFamily,
        fontSize: this.external_containerFontSize,
        fontWeight: this.external_containerFontWeight
      };
    },
    container_dbFont: function() {
      return {
        fontFamily: this.container_dbFontFamily,
        fontSize: this.container_dbFontSize,
        fontWeight: this.container_dbFontWeight
      };
    },
    external_container_dbFont: function() {
      return {
        fontFamily: this.external_container_dbFontFamily,
        fontSize: this.external_container_dbFontSize,
        fontWeight: this.external_container_dbFontWeight
      };
    },
    container_queueFont: function() {
      return {
        fontFamily: this.container_queueFontFamily,
        fontSize: this.container_queueFontSize,
        fontWeight: this.container_queueFontWeight
      };
    },
    external_container_queueFont: function() {
      return {
        fontFamily: this.external_container_queueFontFamily,
        fontSize: this.external_container_queueFontSize,
        fontWeight: this.external_container_queueFontWeight
      };
    },
    componentFont: function() {
      return {
        fontFamily: this.componentFontFamily,
        fontSize: this.componentFontSize,
        fontWeight: this.componentFontWeight
      };
    },
    external_componentFont: function() {
      return {
        fontFamily: this.external_componentFontFamily,
        fontSize: this.external_componentFontSize,
        fontWeight: this.external_componentFontWeight
      };
    },
    component_dbFont: function() {
      return {
        fontFamily: this.component_dbFontFamily,
        fontSize: this.component_dbFontSize,
        fontWeight: this.component_dbFontWeight
      };
    },
    external_component_dbFont: function() {
      return {
        fontFamily: this.external_component_dbFontFamily,
        fontSize: this.external_component_dbFontSize,
        fontWeight: this.external_component_dbFontWeight
      };
    },
    component_queueFont: function() {
      return {
        fontFamily: this.component_queueFontFamily,
        fontSize: this.component_queueFontSize,
        fontWeight: this.component_queueFontWeight
      };
    },
    external_component_queueFont: function() {
      return {
        fontFamily: this.external_component_queueFontFamily,
        fontSize: this.external_component_queueFontSize,
        fontWeight: this.external_component_queueFontWeight
      };
    },
    boundaryFont: function() {
      return {
        fontFamily: this.boundaryFontFamily,
        fontSize: this.boundaryFontSize,
        fontWeight: this.boundaryFontWeight
      };
    },
    messageFont: function() {
      return {
        fontFamily: this.messageFontFamily,
        fontSize: this.messageFontSize,
        fontWeight: this.messageFontWeight
      };
    }
  },
  pie: {
    ...Zt.pie,
    useWidth: 984
  },
  xyChart: {
    ...Zt.xyChart,
    useWidth: void 0
  },
  requirement: {
    ...Zt.requirement,
    useWidth: void 0
  },
  gitGraph: {
    ...Zt.gitGraph,
    // TODO: This is a temporary override for `gitGraph`, since every other
    //       diagram does have `useMaxWidth`, but instead sets it to `true`.
    //       Should we set this to `true` instead?
    useMaxWidth: !1
  },
  sankey: {
    ...Zt.sankey,
    // this is false, unlike every other diagram (other than gitGraph)
    // TODO: can we make this default to `true` instead?
    useMaxWidth: !1
  }
}, el = (t, e = "") => Object.keys(t).reduce((i, r) => Array.isArray(t[r]) ? i : typeof t[r] == "object" && t[r] !== null ? [...i, e + r, ...el(t[r], "")] : [...i, e + r], []), zp = new Set(el(tl, "")), Wp = tl, sr = (t) => {
  if (A.debug("sanitizeDirective called with", t), !(typeof t != "object" || t == null)) {
    if (Array.isArray(t)) {
      t.forEach((e) => sr(e));
      return;
    }
    for (const e of Object.keys(t)) {
      if (A.debug("Checking key", e), e.startsWith("__") || e.includes("proto") || e.includes("constr") || !zp.has(e) || t[e] == null) {
        A.debug("sanitize deleting key: ", e), delete t[e];
        continue;
      }
      if (typeof t[e] == "object") {
        A.debug("sanitizing object", e), sr(t[e]);
        continue;
      }
      const i = ["themeCSS", "fontFamily", "altFontFamily"];
      for (const r of i)
        e.includes(r) && (A.debug("sanitizing css option", e), t[e] = il(t[e]));
    }
    if (t.themeVariables)
      for (const e of Object.keys(t.themeVariables)) {
        const i = t.themeVariables[e];
        i != null && i.match && !i.match(/^[\d "#%(),.;A-Za-z]+$/) && (t.themeVariables[e] = "");
      }
    A.debug("After sanitization", t);
  }
}, il = (t) => {
  let e = 0, i = 0;
  for (const r of t) {
    if (e < i)
      return "{ /* ERROR: Unbalanced CSS */ }";
    r === "{" ? e++ : r === "}" && i++;
  }
  return e !== i ? "{ /* ERROR: Unbalanced CSS */ }" : t;
}, rl = /^-{3}\s*[\n\r](.*?)[\n\r]-{3}\s*[\n\r]+/s, hi = /%{2}{\s*(?:(\w+)\s*:|(\w+))\s*(?:(\w+)|((?:(?!}%{2}).|\r?\n)*))?\s*(?:}%{2})?/gi, Hp = /\s*%%.*\n/gm;
class nl extends Error {
  constructor(e) {
    super(e), this.name = "UnknownDiagramError";
  }
}
const qe = {}, Br = function(t, e) {
  t = t.replace(rl, "").replace(hi, "").replace(Hp, `
`);
  for (const [i, { detector: r }] of Object.entries(qe))
    if (r(t, e))
      return i;
  throw new nl(
    `No diagram type detected matching given configuration for text: ${t}`
  );
}, ol = (...t) => {
  for (const { id: e, detector: i, loader: r } of t)
    sl(e, i, r);
}, sl = (t, e, i) => {
  qe[t] ? A.error(`Detector with key ${t} already exists`) : qe[t] = { detector: e, loader: i }, A.debug(`Detector with key ${t} added${i ? " with loader" : ""}`);
}, jp = (t) => qe[t].loader, kn = (t, e, { depth: i = 2, clobber: r = !1 } = {}) => {
  const n = { depth: i, clobber: r };
  return Array.isArray(e) && !Array.isArray(t) ? (e.forEach((o) => kn(t, o, n)), t) : Array.isArray(e) && Array.isArray(t) ? (e.forEach((o) => {
    t.includes(o) || t.push(o);
  }), t) : t === void 0 || i <= 0 ? t != null && typeof t == "object" && typeof e == "object" ? Object.assign(t, e) : e : (e !== void 0 && typeof t == "object" && typeof e == "object" && Object.keys(e).forEach((o) => {
    typeof e[o] == "object" && (t[o] === void 0 || typeof t[o] == "object") ? (t[o] === void 0 && (t[o] = Array.isArray(e[o]) ? [] : {}), t[o] = kn(t[o], e[o], { depth: i - 1, clobber: r })) : (r || typeof t[o] != "object" && typeof e[o] != "object") && (t[o] = e[o]);
  }), t);
}, ht = kn;
var Up = typeof global == "object" && global && global.Object === Object && global;
const al = Up;
var Yp = typeof self == "object" && self && self.Object === Object && self, Gp = al || Yp || Function("return this")();
const Gt = Gp;
var Vp = Gt.Symbol;
const ar = Vp;
var ll = Object.prototype, Xp = ll.hasOwnProperty, Kp = ll.toString, ii = ar ? ar.toStringTag : void 0;
function Zp(t) {
  var e = Xp.call(t, ii), i = t[ii];
  try {
    t[ii] = void 0;
    var r = !0;
  } catch {
  }
  var n = Kp.call(t);
  return r && (e ? t[ii] = i : delete t[ii]), n;
}
var Jp = Object.prototype, Qp = Jp.toString;
function tg(t) {
  return Qp.call(t);
}
var eg = "[object Null]", ig = "[object Undefined]", xs = ar ? ar.toStringTag : void 0;
function Ue(t) {
  return t == null ? t === void 0 ? ig : eg : xs && xs in Object(t) ? Zp(t) : tg(t);
}
function Te(t) {
  var e = typeof t;
  return t != null && (e == "object" || e == "function");
}
var rg = "[object AsyncFunction]", ng = "[object Function]", og = "[object GeneratorFunction]", sg = "[object Proxy]";
function Kn(t) {
  if (!Te(t))
    return !1;
  var e = Ue(t);
  return e == ng || e == og || e == rg || e == sg;
}
var ag = Gt["__core-js_shared__"];
const on = ag;
var bs = function() {
  var t = /[^.]+$/.exec(on && on.keys && on.keys.IE_PROTO || "");
  return t ? "Symbol(src)_1." + t : "";
}();
function lg(t) {
  return !!bs && bs in t;
}
var hg = Function.prototype, cg = hg.toString;
function Se(t) {
  if (t != null) {
    try {
      return cg.call(t);
    } catch {
    }
    try {
      return t + "";
    } catch {
    }
  }
  return "";
}
var ug = /[\\^$.*+?()[\]{}|]/g, fg = /^\[object .+?Constructor\]$/, dg = Function.prototype, pg = Object.prototype, gg = dg.toString, mg = pg.hasOwnProperty, yg = RegExp(
  "^" + gg.call(mg).replace(ug, "\\$&").replace(/hasOwnProperty|(function).*?(?=\\\()| for .+?(?=\\\])/g, "$1.*?") + "$"
);
function _g(t) {
  if (!Te(t) || lg(t))
    return !1;
  var e = Kn(t) ? yg : fg;
  return e.test(Se(t));
}
function Cg(t, e) {
  return t == null ? void 0 : t[e];
}
function ke(t, e) {
  var i = Cg(t, e);
  return _g(i) ? i : void 0;
}
var xg = ke(Object, "create");
const yi = xg;
function bg() {
  this.__data__ = yi ? yi(null) : {}, this.size = 0;
}
function Tg(t) {
  var e = this.has(t) && delete this.__data__[t];
  return this.size -= e ? 1 : 0, e;
}
var Sg = "__lodash_hash_undefined__", kg = Object.prototype, vg = kg.hasOwnProperty;
function wg(t) {
  var e = this.__data__;
  if (yi) {
    var i = e[t];
    return i === Sg ? void 0 : i;
  }
  return vg.call(e, t) ? e[t] : void 0;
}
var Bg = Object.prototype, Ag = Bg.hasOwnProperty;
function Fg(t) {
  var e = this.__data__;
  return yi ? e[t] !== void 0 : Ag.call(e, t);
}
var Lg = "__lodash_hash_undefined__";
function Eg(t, e) {
  var i = this.__data__;
  return this.size += this.has(t) ? 0 : 1, i[t] = yi && e === void 0 ? Lg : e, this;
}
function xe(t) {
  var e = -1, i = t == null ? 0 : t.length;
  for (this.clear(); ++e < i; ) {
    var r = t[e];
    this.set(r[0], r[1]);
  }
}
xe.prototype.clear = bg;
xe.prototype.delete = Tg;
xe.prototype.get = wg;
xe.prototype.has = Fg;
xe.prototype.set = Eg;
function Og() {
  this.__data__ = [], this.size = 0;
}
function Ar(t, e) {
  return t === e || t !== t && e !== e;
}
function Fr(t, e) {
  for (var i = t.length; i--; )
    if (Ar(t[i][0], e))
      return i;
  return -1;
}
var Mg = Array.prototype, Ig = Mg.splice;
function $g(t) {
  var e = this.__data__, i = Fr(e, t);
  if (i < 0)
    return !1;
  var r = e.length - 1;
  return i == r ? e.pop() : Ig.call(e, i, 1), --this.size, !0;
}
function Dg(t) {
  var e = this.__data__, i = Fr(e, t);
  return i < 0 ? void 0 : e[i][1];
}
function Ng(t) {
  return Fr(this.__data__, t) > -1;
}
function Rg(t, e) {
  var i = this.__data__, r = Fr(i, t);
  return r < 0 ? (++this.size, i.push([t, e])) : i[r][1] = e, this;
}
function ie(t) {
  var e = -1, i = t == null ? 0 : t.length;
  for (this.clear(); ++e < i; ) {
    var r = t[e];
    this.set(r[0], r[1]);
  }
}
ie.prototype.clear = Og;
ie.prototype.delete = $g;
ie.prototype.get = Dg;
ie.prototype.has = Ng;
ie.prototype.set = Rg;
var Pg = ke(Gt, "Map");
const _i = Pg;
function qg() {
  this.size = 0, this.__data__ = {
    hash: new xe(),
    map: new (_i || ie)(),
    string: new xe()
  };
}
function zg(t) {
  var e = typeof t;
  return e == "string" || e == "number" || e == "symbol" || e == "boolean" ? t !== "__proto__" : t === null;
}
function Lr(t, e) {
  var i = t.__data__;
  return zg(e) ? i[typeof e == "string" ? "string" : "hash"] : i.map;
}
function Wg(t) {
  var e = Lr(this, t).delete(t);
  return this.size -= e ? 1 : 0, e;
}
function Hg(t) {
  return Lr(this, t).get(t);
}
function jg(t) {
  return Lr(this, t).has(t);
}
function Ug(t, e) {
  var i = Lr(this, t), r = i.size;
  return i.set(t, e), this.size += i.size == r ? 0 : 1, this;
}
function ce(t) {
  var e = -1, i = t == null ? 0 : t.length;
  for (this.clear(); ++e < i; ) {
    var r = t[e];
    this.set(r[0], r[1]);
  }
}
ce.prototype.clear = qg;
ce.prototype.delete = Wg;
ce.prototype.get = Hg;
ce.prototype.has = jg;
ce.prototype.set = Ug;
var Yg = "Expected a function";
function wi(t, e) {
  if (typeof t != "function" || e != null && typeof e != "function")
    throw new TypeError(Yg);
  var i = function() {
    var r = arguments, n = e ? e.apply(this, r) : r[0], o = i.cache;
    if (o.has(n))
      return o.get(n);
    var s = t.apply(this, r);
    return i.cache = o.set(n, s) || o, s;
  };
  return i.cache = new (wi.Cache || ce)(), i;
}
wi.Cache = ce;
function Gg() {
  this.__data__ = new ie(), this.size = 0;
}
function Vg(t) {
  var e = this.__data__, i = e.delete(t);
  return this.size = e.size, i;
}
function Xg(t) {
  return this.__data__.get(t);
}
function Kg(t) {
  return this.__data__.has(t);
}
var Zg = 200;
function Jg(t, e) {
  var i = this.__data__;
  if (i instanceof ie) {
    var r = i.__data__;
    if (!_i || r.length < Zg - 1)
      return r.push([t, e]), this.size = ++i.size, this;
    i = this.__data__ = new ce(r);
  }
  return i.set(t, e), this.size = i.size, this;
}
function Ye(t) {
  var e = this.__data__ = new ie(t);
  this.size = e.size;
}
Ye.prototype.clear = Gg;
Ye.prototype.delete = Vg;
Ye.prototype.get = Xg;
Ye.prototype.has = Kg;
Ye.prototype.set = Jg;
var Qg = function() {
  try {
    var t = ke(Object, "defineProperty");
    return t({}, "", {}), t;
  } catch {
  }
}();
const lr = Qg;
function Zn(t, e, i) {
  e == "__proto__" && lr ? lr(t, e, {
    configurable: !0,
    enumerable: !0,
    value: i,
    writable: !0
  }) : t[e] = i;
}
function vn(t, e, i) {
  (i !== void 0 && !Ar(t[e], i) || i === void 0 && !(e in t)) && Zn(t, e, i);
}
function tm(t) {
  return function(e, i, r) {
    for (var n = -1, o = Object(e), s = r(e), a = s.length; a--; ) {
      var l = s[t ? a : ++n];
      if (i(o[l], l, o) === !1)
        break;
    }
    return e;
  };
}
var em = tm();
const im = em;
var hl = typeof exports == "object" && exports && !exports.nodeType && exports, Ts = hl && typeof module == "object" && module && !module.nodeType && module, rm = Ts && Ts.exports === hl, Ss = rm ? Gt.Buffer : void 0, ks = Ss ? Ss.allocUnsafe : void 0;
function nm(t, e) {
  if (e)
    return t.slice();
  var i = t.length, r = ks ? ks(i) : new t.constructor(i);
  return t.copy(r), r;
}
var om = Gt.Uint8Array;
const vs = om;
function sm(t) {
  var e = new t.constructor(t.byteLength);
  return new vs(e).set(new vs(t)), e;
}
function am(t, e) {
  var i = e ? sm(t.buffer) : t.buffer;
  return new t.constructor(i, t.byteOffset, t.length);
}
function lm(t, e) {
  var i = -1, r = t.length;
  for (e || (e = Array(r)); ++i < r; )
    e[i] = t[i];
  return e;
}
var ws = Object.create, hm = function() {
  function t() {
  }
  return function(e) {
    if (!Te(e))
      return {};
    if (ws)
      return ws(e);
    t.prototype = e;
    var i = new t();
    return t.prototype = void 0, i;
  };
}();
const cm = hm;
function cl(t, e) {
  return function(i) {
    return t(e(i));
  };
}
var um = cl(Object.getPrototypeOf, Object);
const ul = um;
var fm = Object.prototype;
function Er(t) {
  var e = t && t.constructor, i = typeof e == "function" && e.prototype || fm;
  return t === i;
}
function dm(t) {
  return typeof t.constructor == "function" && !Er(t) ? cm(ul(t)) : {};
}
function Bi(t) {
  return t != null && typeof t == "object";
}
var pm = "[object Arguments]";
function Bs(t) {
  return Bi(t) && Ue(t) == pm;
}
var fl = Object.prototype, gm = fl.hasOwnProperty, mm = fl.propertyIsEnumerable, ym = Bs(function() {
  return arguments;
}()) ? Bs : function(t) {
  return Bi(t) && gm.call(t, "callee") && !mm.call(t, "callee");
};
const hr = ym;
var _m = Array.isArray;
const cr = _m;
var Cm = 9007199254740991;
function dl(t) {
  return typeof t == "number" && t > -1 && t % 1 == 0 && t <= Cm;
}
function Or(t) {
  return t != null && dl(t.length) && !Kn(t);
}
function xm(t) {
  return Bi(t) && Or(t);
}
function bm() {
  return !1;
}
var pl = typeof exports == "object" && exports && !exports.nodeType && exports, As = pl && typeof module == "object" && module && !module.nodeType && module, Tm = As && As.exports === pl, Fs = Tm ? Gt.Buffer : void 0, Sm = Fs ? Fs.isBuffer : void 0, km = Sm || bm;
const Jn = km;
var vm = "[object Object]", wm = Function.prototype, Bm = Object.prototype, gl = wm.toString, Am = Bm.hasOwnProperty, Fm = gl.call(Object);
function Lm(t) {
  if (!Bi(t) || Ue(t) != vm)
    return !1;
  var e = ul(t);
  if (e === null)
    return !0;
  var i = Am.call(e, "constructor") && e.constructor;
  return typeof i == "function" && i instanceof i && gl.call(i) == Fm;
}
var Em = "[object Arguments]", Om = "[object Array]", Mm = "[object Boolean]", Im = "[object Date]", $m = "[object Error]", Dm = "[object Function]", Nm = "[object Map]", Rm = "[object Number]", Pm = "[object Object]", qm = "[object RegExp]", zm = "[object Set]", Wm = "[object String]", Hm = "[object WeakMap]", jm = "[object ArrayBuffer]", Um = "[object DataView]", Ym = "[object Float32Array]", Gm = "[object Float64Array]", Vm = "[object Int8Array]", Xm = "[object Int16Array]", Km = "[object Int32Array]", Zm = "[object Uint8Array]", Jm = "[object Uint8ClampedArray]", Qm = "[object Uint16Array]", t0 = "[object Uint32Array]", X = {};
X[Ym] = X[Gm] = X[Vm] = X[Xm] = X[Km] = X[Zm] = X[Jm] = X[Qm] = X[t0] = !0;
X[Em] = X[Om] = X[jm] = X[Mm] = X[Um] = X[Im] = X[$m] = X[Dm] = X[Nm] = X[Rm] = X[Pm] = X[qm] = X[zm] = X[Wm] = X[Hm] = !1;
function e0(t) {
  return Bi(t) && dl(t.length) && !!X[Ue(t)];
}
function i0(t) {
  return function(e) {
    return t(e);
  };
}
var ml = typeof exports == "object" && exports && !exports.nodeType && exports, ci = ml && typeof module == "object" && module && !module.nodeType && module, r0 = ci && ci.exports === ml, sn = r0 && al.process, n0 = function() {
  try {
    var t = ci && ci.require && ci.require("util").types;
    return t || sn && sn.binding && sn.binding("util");
  } catch {
  }
}();
const Ls = n0;
var Es = Ls && Ls.isTypedArray, o0 = Es ? i0(Es) : e0;
const Qn = o0;
function wn(t, e) {
  if (!(e === "constructor" && typeof t[e] == "function") && e != "__proto__")
    return t[e];
}
var s0 = Object.prototype, a0 = s0.hasOwnProperty;
function l0(t, e, i) {
  var r = t[e];
  (!(a0.call(t, e) && Ar(r, i)) || i === void 0 && !(e in t)) && Zn(t, e, i);
}
function h0(t, e, i, r) {
  var n = !i;
  i || (i = {});
  for (var o = -1, s = e.length; ++o < s; ) {
    var a = e[o], l = r ? r(i[a], t[a], a, i, t) : void 0;
    l === void 0 && (l = t[a]), n ? Zn(i, a, l) : l0(i, a, l);
  }
  return i;
}
function c0(t, e) {
  for (var i = -1, r = Array(t); ++i < t; )
    r[i] = e(i);
  return r;
}
var u0 = 9007199254740991, f0 = /^(?:0|[1-9]\d*)$/;
function yl(t, e) {
  var i = typeof t;
  return e = e ?? u0, !!e && (i == "number" || i != "symbol" && f0.test(t)) && t > -1 && t % 1 == 0 && t < e;
}
var d0 = Object.prototype, p0 = d0.hasOwnProperty;
function g0(t, e) {
  var i = cr(t), r = !i && hr(t), n = !i && !r && Jn(t), o = !i && !r && !n && Qn(t), s = i || r || n || o, a = s ? c0(t.length, String) : [], l = a.length;
  for (var h in t)
    (e || p0.call(t, h)) && !(s && // Safari 9 has enumerable `arguments.length` in strict mode.
    (h == "length" || // Node.js 0.10 has enumerable non-index properties on buffers.
    n && (h == "offset" || h == "parent") || // PhantomJS 2 has enumerable non-index properties on typed arrays.
    o && (h == "buffer" || h == "byteLength" || h == "byteOffset") || // Skip index properties.
    yl(h, l))) && a.push(h);
  return a;
}
function m0(t) {
  var e = [];
  if (t != null)
    for (var i in Object(t))
      e.push(i);
  return e;
}
var y0 = Object.prototype, _0 = y0.hasOwnProperty;
function C0(t) {
  if (!Te(t))
    return m0(t);
  var e = Er(t), i = [];
  for (var r in t)
    r == "constructor" && (e || !_0.call(t, r)) || i.push(r);
  return i;
}
function _l(t) {
  return Or(t) ? g0(t, !0) : C0(t);
}
function x0(t) {
  return h0(t, _l(t));
}
function b0(t, e, i, r, n, o, s) {
  var a = wn(t, i), l = wn(e, i), h = s.get(l);
  if (h) {
    vn(t, i, h);
    return;
  }
  var u = o ? o(a, l, i + "", t, e, s) : void 0, d = u === void 0;
  if (d) {
    var c = cr(l), p = !c && Jn(l), y = !c && !p && Qn(l);
    u = l, c || p || y ? cr(a) ? u = a : xm(a) ? u = lm(a) : p ? (d = !1, u = nm(l, !0)) : y ? (d = !1, u = am(l, !0)) : u = [] : Lm(l) || hr(l) ? (u = a, hr(a) ? u = x0(a) : (!Te(a) || Kn(a)) && (u = dm(l))) : d = !1;
  }
  d && (s.set(l, u), n(u, l, r, o, s), s.delete(l)), vn(t, i, u);
}
function Cl(t, e, i, r, n) {
  t !== e && im(e, function(o, s) {
    if (n || (n = new Ye()), Te(o))
      b0(t, e, s, i, Cl, r, n);
    else {
      var a = r ? r(wn(t, s), o, s + "", t, e, n) : void 0;
      a === void 0 && (a = o), vn(t, s, a);
    }
  }, _l);
}
function xl(t) {
  return t;
}
function T0(t, e, i) {
  switch (i.length) {
    case 0:
      return t.call(e);
    case 1:
      return t.call(e, i[0]);
    case 2:
      return t.call(e, i[0], i[1]);
    case 3:
      return t.call(e, i[0], i[1], i[2]);
  }
  return t.apply(e, i);
}
var Os = Math.max;
function S0(t, e, i) {
  return e = Os(e === void 0 ? t.length - 1 : e, 0), function() {
    for (var r = arguments, n = -1, o = Os(r.length - e, 0), s = Array(o); ++n < o; )
      s[n] = r[e + n];
    n = -1;
    for (var a = Array(e + 1); ++n < e; )
      a[n] = r[n];
    return a[e] = i(s), T0(t, this, a);
  };
}
function k0(t) {
  return function() {
    return t;
  };
}
var v0 = lr ? function(t, e) {
  return lr(t, "toString", {
    configurable: !0,
    enumerable: !1,
    value: k0(e),
    writable: !0
  });
} : xl;
const w0 = v0;
var B0 = 800, A0 = 16, F0 = Date.now;
function L0(t) {
  var e = 0, i = 0;
  return function() {
    var r = F0(), n = A0 - (r - i);
    if (i = r, n > 0) {
      if (++e >= B0)
        return arguments[0];
    } else
      e = 0;
    return t.apply(void 0, arguments);
  };
}
var E0 = L0(w0);
const O0 = E0;
function M0(t, e) {
  return O0(S0(t, e, xl), t + "");
}
function I0(t, e, i) {
  if (!Te(i))
    return !1;
  var r = typeof e;
  return (r == "number" ? Or(i) && yl(e, i.length) : r == "string" && e in i) ? Ar(i[e], t) : !1;
}
function $0(t) {
  return M0(function(e, i) {
    var r = -1, n = i.length, o = n > 1 ? i[n - 1] : void 0, s = n > 2 ? i[2] : void 0;
    for (o = t.length > 3 && typeof o == "function" ? (n--, o) : void 0, s && I0(i[0], i[1], s) && (o = n < 3 ? void 0 : o, n = 1), e = Object(e); ++r < n; ) {
      var a = i[r];
      a && t(e, a, r, o);
    }
    return e;
  });
}
var D0 = $0(function(t, e, i) {
  Cl(t, e, i);
});
const N0 = D0, R0 = "​", P0 = {
  curveBasis: nd,
  curveBasisClosed: od,
  curveBasisOpen: sd,
  curveBumpX: id,
  curveBumpY: rd,
  curveBundle: ad,
  curveCardinalClosed: hd,
  curveCardinalOpen: cd,
  curveCardinal: ld,
  curveCatmullRomClosed: fd,
  curveCatmullRomOpen: dd,
  curveCatmullRom: ud,
  curveLinear: ed,
  curveLinearClosed: pd,
  curveMonotoneX: gd,
  curveMonotoneY: md,
  curveNatural: yd,
  curveStep: _d,
  curveStepAfter: xd,
  curveStepBefore: Cd
}, q0 = /\s*(?:(\w+)(?=:):|(\w+))\s*(?:(\w+)|((?:(?!}%{2}).|\r?\n)*))?\s*(?:}%{2})?/gi, z0 = function(t, e) {
  const i = bl(t, /(?:init\b)|(?:initialize\b)/);
  let r = {};
  if (Array.isArray(i)) {
    const s = i.map((a) => a.args);
    sr(s), r = ht(r, [...s]);
  } else
    r = i.args;
  if (!r)
    return;
  let n = Br(t, e);
  const o = "config";
  return r[o] !== void 0 && (n === "flowchart-v2" && (n = "flowchart"), r[n] = r[o], delete r[o]), r;
}, bl = function(t, e = null) {
  try {
    const i = new RegExp(
      `[%]{2}(?![{]${q0.source})(?=[}][%]{2}).*
`,
      "ig"
    );
    t = t.trim().replace(i, "").replace(/'/gm, '"'), A.debug(
      `Detecting diagram directive${e !== null ? " type:" + e : ""} based on the text:${t}`
    );
    let r;
    const n = [];
    for (; (r = hi.exec(t)) !== null; )
      if (r.index === hi.lastIndex && hi.lastIndex++, r && !e || e && r[1] && r[1].match(e) || e && r[2] && r[2].match(e)) {
        const o = r[1] ? r[1] : r[2], s = r[3] ? r[3].trim() : r[4] ? JSON.parse(r[4].trim()) : null;
        n.push({ type: o, args: s });
      }
    return n.length === 0 ? { type: t, args: null } : n.length === 1 ? n[0] : n;
  } catch (i) {
    return A.error(
      `ERROR: ${i.message} - Unable to parse directive type: '${e}' based on the text: '${t}'`
    ), { type: void 0, args: null };
  }
}, W0 = function(t) {
  return t.replace(hi, "");
}, H0 = function(t, e) {
  for (const [i, r] of e.entries())
    if (r.match(t))
      return i;
  return -1;
};
function j0(t, e) {
  if (!t)
    return e;
  const i = `curve${t.charAt(0).toUpperCase() + t.slice(1)}`;
  return P0[i] ?? e;
}
function U0(t, e) {
  const i = t.trim();
  if (i)
    return e.securityLevel !== "loose" ? na.sanitizeUrl(i) : i;
}
const Y0 = (t, ...e) => {
  const i = t.split("."), r = i.length - 1, n = i[r];
  let o = window;
  for (let s = 0; s < r; s++)
    if (o = o[i[s]], !o) {
      A.error(`Function name: ${t} not found in window`);
      return;
    }
  o[n](...e);
};
function Tl(t, e) {
  return !t || !e ? 0 : Math.sqrt(Math.pow(e.x - t.x, 2) + Math.pow(e.y - t.y, 2));
}
function G0(t) {
  let e, i = 0;
  t.forEach((n) => {
    i += Tl(n, e), e = n;
  });
  const r = i / 2;
  return to(t, r);
}
function V0(t) {
  return t.length === 1 ? t[0] : G0(t);
}
const Ms = (t, e = 2) => {
  const i = Math.pow(10, e);
  return Math.round(t * i) / i;
}, to = (t, e) => {
  let i, r = e;
  for (const n of t) {
    if (i) {
      const o = Tl(n, i);
      if (o < r)
        r -= o;
      else {
        const s = r / o;
        if (s <= 0)
          return i;
        if (s >= 1)
          return { x: n.x, y: n.y };
        if (s > 0 && s < 1)
          return {
            x: Ms((1 - s) * i.x + s * n.x, 5),
            y: Ms((1 - s) * i.y + s * n.y, 5)
          };
      }
    }
    i = n;
  }
  throw new Error("Could not find a suitable point for the given distance");
}, X0 = (t, e, i) => {
  A.info(`our points ${JSON.stringify(e)}`), e[0] !== i && (e = e.reverse());
  const n = to(e, 25), o = t ? 10 : 5, s = Math.atan2(e[0].y - n.y, e[0].x - n.x), a = { x: 0, y: 0 };
  return a.x = Math.sin(s) * o + (e[0].x + n.x) / 2, a.y = -Math.cos(s) * o + (e[0].y + n.y) / 2, a;
};
function K0(t, e, i) {
  const r = structuredClone(i);
  A.info("our points", r), e !== "start_left" && e !== "start_right" && r.reverse();
  const n = 25 + t, o = to(r, n), s = 10 + t * 0.5, a = Math.atan2(r[0].y - o.y, r[0].x - o.x), l = { x: 0, y: 0 };
  return e === "start_left" ? (l.x = Math.sin(a + Math.PI) * s + (r[0].x + o.x) / 2, l.y = -Math.cos(a + Math.PI) * s + (r[0].y + o.y) / 2) : e === "end_right" ? (l.x = Math.sin(a - Math.PI) * s + (r[0].x + o.x) / 2 - 5, l.y = -Math.cos(a - Math.PI) * s + (r[0].y + o.y) / 2 - 5) : e === "end_left" ? (l.x = Math.sin(a) * s + (r[0].x + o.x) / 2 - 5, l.y = -Math.cos(a) * s + (r[0].y + o.y) / 2 - 5) : (l.x = Math.sin(a) * s + (r[0].x + o.x) / 2, l.y = -Math.cos(a) * s + (r[0].y + o.y) / 2), l;
}
function Z0(t) {
  let e = "", i = "";
  for (const r of t)
    r !== void 0 && (r.startsWith("color:") || r.startsWith("text-align:") ? i = i + r + ";" : e = e + r + ";");
  return { style: e, labelStyle: i };
}
let Is = 0;
const J0 = () => (Is++, "id-" + Math.random().toString(36).substr(2, 12) + "-" + Is);
function Q0(t) {
  let e = "";
  const i = "0123456789abcdef", r = i.length;
  for (let n = 0; n < t; n++)
    e += i.charAt(Math.floor(Math.random() * r));
  return e;
}
const ty = (t) => Q0(t.length), ey = function() {
  return {
    x: 0,
    y: 0,
    fill: void 0,
    anchor: "start",
    style: "#666",
    width: 100,
    height: 100,
    textMargin: 0,
    rx: 0,
    ry: 0,
    valign: void 0,
    text: ""
  };
}, iy = function(t, e) {
  const i = e.text.replace(Xn.lineBreakRegex, " "), [, r] = io(e.fontSize), n = t.append("text");
  n.attr("x", e.x), n.attr("y", e.y), n.style("text-anchor", e.anchor), n.style("font-family", e.fontFamily), n.style("font-size", r), n.style("font-weight", e.fontWeight), n.attr("fill", e.fill), e.class !== void 0 && n.attr("class", e.class);
  const o = n.append("tspan");
  return o.attr("x", e.x + e.textMargin * 2), o.attr("fill", e.fill), o.text(i), n;
}, ry = wi(
  (t, e, i) => {
    if (!t || (i = Object.assign(
      { fontSize: 12, fontWeight: 400, fontFamily: "Arial", joinWith: "<br/>" },
      i
    ), Xn.lineBreakRegex.test(t)))
      return t;
    const r = t.split(" "), n = [];
    let o = "";
    return r.forEach((s, a) => {
      const l = ur(`${s} `, i), h = ur(o, i);
      if (l > e) {
        const { hyphenatedStrings: c, remainingWord: p } = ny(s, e, "-", i);
        n.push(o, ...c), o = p;
      } else
        h + l >= e ? (n.push(o), o = s) : o = [o, s].filter(Boolean).join(" ");
      a + 1 === r.length && n.push(o);
    }), n.filter((s) => s !== "").join(i.joinWith);
  },
  (t, e, i) => `${t}${e}${i.fontSize}${i.fontWeight}${i.fontFamily}${i.joinWith}`
), ny = wi(
  (t, e, i = "-", r) => {
    r = Object.assign(
      { fontSize: 12, fontWeight: 400, fontFamily: "Arial", margin: 0 },
      r
    );
    const n = [...t], o = [];
    let s = "";
    return n.forEach((a, l) => {
      const h = `${s}${a}`;
      if (ur(h, r) >= e) {
        const d = l + 1, c = n.length === d, p = `${h}${i}`;
        o.push(c ? h : p), s = "";
      } else
        s = h;
    }), { hyphenatedStrings: o, remainingWord: s };
  },
  (t, e, i = "-", r) => `${t}${e}${i}${r.fontSize}${r.fontWeight}${r.fontFamily}`
);
function oy(t, e) {
  return eo(t, e).height;
}
function ur(t, e) {
  return eo(t, e).width;
}
const eo = wi(
  (t, e) => {
    const { fontSize: i = 12, fontFamily: r = "Arial", fontWeight: n = 400 } = e;
    if (!t)
      return { width: 0, height: 0 };
    const [, o] = io(i), s = ["sans-serif", r], a = t.split(Xn.lineBreakRegex), l = [], h = At("body");
    if (!h.remove)
      return { width: 0, height: 0, lineHeight: 0 };
    const u = h.append("svg");
    for (const c of s) {
      let p = 0;
      const y = { width: 0, height: 0, lineHeight: 0 };
      for (const S of a) {
        const M = ey();
        M.text = S || R0;
        const q = iy(u, M).style("font-size", o).style("font-weight", n).style("font-family", c), T = (q._groups || q)[0][0].getBBox();
        if (T.width === 0 && T.height === 0)
          throw new Error("svg element not in render tree");
        y.width = Math.round(Math.max(y.width, T.width)), p = Math.round(T.height), y.height += p, y.lineHeight = Math.round(Math.max(y.lineHeight, p));
      }
      l.push(y);
    }
    u.remove();
    const d = isNaN(l[1].height) || isNaN(l[1].width) || isNaN(l[1].lineHeight) || l[0].height > l[1].height && l[0].width > l[1].width && l[0].lineHeight > l[1].lineHeight ? 0 : 1;
    return l[d];
  },
  (t, e) => `${t}${e.fontSize}${e.fontWeight}${e.fontFamily}`
);
class sy {
  constructor(e = !1, i) {
    this.count = 0, this.count = i ? i.length : 0, this.next = e ? () => this.count++ : () => Date.now();
  }
}
let Pi;
const ay = function(t) {
  return Pi = Pi || document.createElement("div"), t = escape(t).replace(/%26/g, "&").replace(/%23/g, "#").replace(/%3B/g, ";"), Pi.innerHTML = t, unescape(Pi.textContent);
};
function Sl(t) {
  return "str" in t;
}
const ly = (t, e, i, r) => {
  var o;
  if (!r)
    return;
  const n = (o = t.node()) == null ? void 0 : o.getBBox();
  n && t.append("text").text(r).attr("x", n.x + n.width / 2).attr("y", -i).attr("class", e);
}, io = (t) => {
  if (typeof t == "number")
    return [t, t + "px"];
  const e = parseInt(t ?? "", 10);
  return Number.isNaN(e) ? [void 0, void 0] : t === String(e) ? [e, t + "px"] : [e, t];
};
function kl(t, e) {
  return N0({}, t, e);
}
const ui = {
  assignWithDepth: ht,
  wrapLabel: ry,
  calculateTextHeight: oy,
  calculateTextWidth: ur,
  calculateTextDimensions: eo,
  cleanAndMerge: kl,
  detectInit: z0,
  detectDirective: bl,
  isSubstringInArray: H0,
  interpolateToCurve: j0,
  calcLabelPosition: V0,
  calcCardinalityPosition: X0,
  calcTerminalLabelPosition: K0,
  formatUrl: U0,
  getStylesFromArray: Z0,
  generateId: J0,
  random: ty,
  runFunc: Y0,
  entityDecode: ay,
  insertTitle: ly,
  parseFontSize: io,
  InitIDGenerator: sy
}, hy = function(t) {
  let e = t;
  return e = e.replace(/style.*:\S*#.*;/g, function(i) {
    return i.substring(0, i.length - 1);
  }), e = e.replace(/classDef.*:\S*#.*;/g, function(i) {
    return i.substring(0, i.length - 1);
  }), e = e.replace(/#\w+;/g, function(i) {
    const r = i.substring(1, i.length - 1);
    return /^\+?\d+$/.test(r) ? "ﬂ°°" + r + "¶ß" : "ﬂ°" + r + "¶ß";
  }), e;
}, cy = function(t) {
  return t.replace(/ﬂ°°/g, "&#").replace(/ﬂ°/g, "&").replace(/¶ß/g, ";");
};
var ro = "comm", vl = "rule", wl = "decl", uy = "@media", fy = "@import", dy = "@supports", Bn = "@keyframes", Bl = "@layer", Al = Math.abs, no = String.fromCharCode;
function Fl(t) {
  return t.trim();
}
function Gi(t, e, i) {
  return t.replace(e, i);
}
function py(t, e, i) {
  return t.indexOf(e, i);
}
function Ci(t, e) {
  return t.charCodeAt(e) | 0;
}
function xi(t, e, i) {
  return t.slice(e, i);
}
function Jt(t) {
  return t.length;
}
function Ll(t) {
  return t.length;
}
function qi(t, e) {
  return e.push(t), t;
}
var Mr = 1, ze = 1, El = 0, Et = 0, Q = 0, Ge = "";
function oo(t, e, i, r, n, o, s, a) {
  return { value: t, root: e, parent: i, type: r, props: n, children: o, line: Mr, column: ze, length: s, return: "", siblings: a };
}
function gy() {
  return Q;
}
function my() {
  return Q = Et > 0 ? Ci(Ge, --Et) : 0, ze--, Q === 10 && (ze = 1, Mr--), Q;
}
function Dt() {
  return Q = Et < El ? Ci(Ge, Et++) : 0, ze++, Q === 10 && (ze = 1, Mr++), Q;
}
function ye() {
  return Ci(Ge, Et);
}
function Vi() {
  return Et;
}
function Ir(t, e) {
  return xi(Ge, t, e);
}
function An(t) {
  switch (t) {
    case 0:
    case 9:
    case 10:
    case 13:
    case 32:
      return 5;
    case 33:
    case 43:
    case 44:
    case 47:
    case 62:
    case 64:
    case 126:
    case 59:
    case 123:
    case 125:
      return 4;
    case 58:
      return 3;
    case 34:
    case 39:
    case 40:
    case 91:
      return 2;
    case 41:
    case 93:
      return 1;
  }
  return 0;
}
function yy(t) {
  return Mr = ze = 1, El = Jt(Ge = t), Et = 0, [];
}
function _y(t) {
  return Ge = "", t;
}
function an(t) {
  return Fl(Ir(Et - 1, Fn(t === 91 ? t + 2 : t === 40 ? t + 1 : t)));
}
function Cy(t) {
  for (; (Q = ye()) && Q < 33; )
    Dt();
  return An(t) > 2 || An(Q) > 3 ? "" : " ";
}
function xy(t, e) {
  for (; --e && Dt() && !(Q < 48 || Q > 102 || Q > 57 && Q < 65 || Q > 70 && Q < 97); )
    ;
  return Ir(t, Vi() + (e < 6 && ye() == 32 && Dt() == 32));
}
function Fn(t) {
  for (; Dt(); )
    switch (Q) {
      case t:
        return Et;
      case 34:
      case 39:
        t !== 34 && t !== 39 && Fn(Q);
        break;
      case 40:
        t === 41 && Fn(t);
        break;
      case 92:
        Dt();
        break;
    }
  return Et;
}
function by(t, e) {
  for (; Dt() && t + Q !== 47 + 10; )
    if (t + Q === 42 + 42 && ye() === 47)
      break;
  return "/*" + Ir(e, Et - 1) + "*" + no(t === 47 ? t : Dt());
}
function Ty(t) {
  for (; !An(ye()); )
    Dt();
  return Ir(t, Et);
}
function Sy(t) {
  return _y(Xi("", null, null, null, [""], t = yy(t), 0, [0], t));
}
function Xi(t, e, i, r, n, o, s, a, l) {
  for (var h = 0, u = 0, d = s, c = 0, p = 0, y = 0, S = 1, M = 1, q = 1, T = 0, U = "", W = n, G = o, V = r, H = U; M; )
    switch (y = T, T = Dt()) {
      case 40:
        if (y != 108 && Ci(H, d - 1) == 58) {
          py(H += Gi(an(T), "&", "&\f"), "&\f", Al(h ? a[h - 1] : 0)) != -1 && (q = -1);
          break;
        }
      case 34:
      case 39:
      case 91:
        H += an(T);
        break;
      case 9:
      case 10:
      case 13:
      case 32:
        H += Cy(y);
        break;
      case 92:
        H += xy(Vi() - 1, 7);
        continue;
      case 47:
        switch (ye()) {
          case 42:
          case 47:
            qi(ky(by(Dt(), Vi()), e, i, l), l);
            break;
          default:
            H += "/";
        }
        break;
      case 123 * S:
        a[h++] = Jt(H) * q;
      case 125 * S:
      case 59:
      case 0:
        switch (T) {
          case 0:
          case 125:
            M = 0;
          case 59 + u:
            q == -1 && (H = Gi(H, /\f/g, "")), p > 0 && Jt(H) - d && qi(p > 32 ? Ds(H + ";", r, i, d - 1, l) : Ds(Gi(H, " ", "") + ";", r, i, d - 2, l), l);
            break;
          case 59:
            H += ";";
          default:
            if (qi(V = $s(H, e, i, h, u, n, a, U, W = [], G = [], d, o), o), T === 123)
              if (u === 0)
                Xi(H, e, V, V, W, o, d, a, G);
              else
                switch (c === 99 && Ci(H, 3) === 110 ? 100 : c) {
                  case 100:
                  case 108:
                  case 109:
                  case 115:
                    Xi(t, V, V, r && qi($s(t, V, V, 0, 0, n, a, U, n, W = [], d, G), G), n, G, d, a, r ? W : G);
                    break;
                  default:
                    Xi(H, V, V, V, [""], G, 0, a, G);
                }
        }
        h = u = p = 0, S = q = 1, U = H = "", d = s;
        break;
      case 58:
        d = 1 + Jt(H), p = y;
      default:
        if (S < 1) {
          if (T == 123)
            --S;
          else if (T == 125 && S++ == 0 && my() == 125)
            continue;
        }
        switch (H += no(T), T * S) {
          case 38:
            q = u > 0 ? 1 : (H += "\f", -1);
            break;
          case 44:
            a[h++] = (Jt(H) - 1) * q, q = 1;
            break;
          case 64:
            ye() === 45 && (H += an(Dt())), c = ye(), u = d = Jt(U = H += Ty(Vi())), T++;
            break;
          case 45:
            y === 45 && Jt(H) == 2 && (S = 0);
        }
    }
  return o;
}
function $s(t, e, i, r, n, o, s, a, l, h, u, d) {
  for (var c = n - 1, p = n === 0 ? o : [""], y = Ll(p), S = 0, M = 0, q = 0; S < r; ++S)
    for (var T = 0, U = xi(t, c + 1, c = Al(M = s[S])), W = t; T < y; ++T)
      (W = Fl(M > 0 ? p[T] + " " + U : Gi(U, /&\f/g, p[T]))) && (l[q++] = W);
  return oo(t, e, i, n === 0 ? vl : a, l, h, u, d);
}
function ky(t, e, i, r) {
  return oo(t, e, i, ro, no(gy()), xi(t, 2, -2), 0, r);
}
function Ds(t, e, i, r, n) {
  return oo(t, e, i, wl, xi(t, 0, r), xi(t, r + 1, -1), r, n);
}
function Ln(t, e) {
  for (var i = "", r = 0; r < t.length; r++)
    i += e(t[r], r, t, e) || "";
  return i;
}
function vy(t, e, i, r) {
  switch (t.type) {
    case Bl:
      if (t.children.length)
        break;
    case fy:
    case wl:
      return t.return = t.return || t.value;
    case ro:
      return "";
    case Bn:
      return t.return = t.value + "{" + Ln(t.children, r) + "}";
    case vl:
      if (!Jt(t.value = t.props.join(",")))
        return "";
  }
  return Jt(i = Ln(t.children, r)) ? t.return = t.value + "{" + i + "}" : "";
}
function wy(t) {
  var e = Ll(t);
  return function(i, r, n, o) {
    for (var s = "", a = 0; a < e; a++)
      s += t[a](i, r, n, o) || "";
    return s;
  };
}
const Ns = "10.9.6", We = Object.freeze(Wp);
let _t = ht({}, We), Ol, He = [], fi = ht({}, We);
const $r = (t, e) => {
  let i = ht({}, t), r = {};
  for (const n of e)
    $l(n), r = ht(r, n);
  if (i = ht(i, r), r.theme && r.theme in te) {
    const n = ht({}, Ol), o = ht(
      n.themeVariables || {},
      r.themeVariables
    );
    i.theme && i.theme in te && (i.themeVariables = te[i.theme].getThemeVariables(o));
  }
  return fi = i, Dl(fi), fi;
}, By = (t) => (_t = ht({}, We), _t = ht(_t, t), t.theme && te[t.theme] && (_t.themeVariables = te[t.theme].getThemeVariables(t.themeVariables)), $r(_t, He), _t), Ay = (t) => {
  Ol = ht({}, t);
}, Fy = (t) => (_t = ht(_t, t), $r(_t, He), _t), Ml = () => ht({}, _t), Il = (t) => (Dl(t), ht(fi, t), Ut()), Ut = () => ht({}, fi), $l = (t) => {
  t && (["secure", ..._t.secure ?? []].forEach((e) => {
    Object.hasOwn(t, e) && (A.debug(`Denied attempt to modify a secure key ${e}`, t[e]), delete t[e]);
  }), Object.keys(t).forEach((e) => {
    e.startsWith("__") && delete t[e];
  }), Object.keys(t).forEach((e) => {
    typeof t[e] == "string" && (t[e].includes("<") || t[e].includes(">") || t[e].includes("url(data:")) && delete t[e], typeof t[e] == "object" && $l(t[e]);
  }));
}, Ly = (t) => {
  sr(t), t.fontFamily && (!t.themeVariables || !t.themeVariables.fontFamily) && (t.themeVariables = { fontFamily: t.fontFamily }), He.push(t), $r(_t, He);
}, fr = (t = _t) => {
  He = [], $r(t, He);
}, Ey = {
  LAZY_LOAD_DEPRECATED: "The configuration options lazyLoadedDiagrams and loadExternalDiagramsAtStartup are deprecated. Please use registerExternalDiagrams instead."
}, Rs = {}, Oy = (t) => {
  Rs[t] || (A.warn(Ey[t]), Rs[t] = !0);
}, Dl = (t) => {
  t && (t.lazyLoadedDiagrams || t.loadExternalDiagramsAtStartup) && Oy("LAZY_LOAD_DEPRECATED");
}, Nl = "c4", My = (t) => /^\s*C4Context|C4Container|C4Component|C4Dynamic|C4Deployment/.test(t), Iy = async () => {
  const { diagram: t } = await import("./c4Diagram-2531b94e.js");
  return { id: Nl, diagram: t };
}, $y = {
  id: Nl,
  detector: My,
  loader: Iy
}, Dy = $y, Rl = "flowchart", Ny = (t, e) => {
  var i, r;
  return ((i = e == null ? void 0 : e.flowchart) == null ? void 0 : i.defaultRenderer) === "dagre-wrapper" || ((r = e == null ? void 0 : e.flowchart) == null ? void 0 : r.defaultRenderer) === "elk" ? !1 : /^\s*graph/.test(t);
}, Ry = async () => {
  const { diagram: t } = await import("./flowDiagram-399972d6.js");
  return { id: Rl, diagram: t };
}, Py = {
  id: Rl,
  detector: Ny,
  loader: Ry
}, qy = Py, Pl = "flowchart-v2", zy = (t, e) => {
  var i, r, n;
  return ((i = e == null ? void 0 : e.flowchart) == null ? void 0 : i.defaultRenderer) === "dagre-d3" || ((r = e == null ? void 0 : e.flowchart) == null ? void 0 : r.defaultRenderer) === "elk" ? !1 : /^\s*graph/.test(t) && ((n = e == null ? void 0 : e.flowchart) == null ? void 0 : n.defaultRenderer) === "dagre-wrapper" ? !0 : /^\s*flowchart/.test(t);
}, Wy = async () => {
  const { diagram: t } = await import("./flowDiagram-v2-4a415998.js");
  return { id: Pl, diagram: t };
}, Hy = {
  id: Pl,
  detector: zy,
  loader: Wy
}, jy = Hy, ql = "er", Uy = (t) => /^\s*erDiagram/.test(t), Yy = async () => {
  const { diagram: t } = await import("./erDiagram-13f45bc1.js");
  return { id: ql, diagram: t };
}, Gy = {
  id: ql,
  detector: Uy,
  loader: Yy
}, Vy = Gy, zl = "gitGraph", Xy = (t) => /^\s*gitGraph/.test(t), Ky = async () => {
  const { diagram: t } = await import("./gitGraphDiagram-958b5573.js");
  return { id: zl, diagram: t };
}, Zy = {
  id: zl,
  detector: Xy,
  loader: Ky
}, Jy = Zy, Wl = "gantt", Qy = (t) => /^\s*gantt/.test(t), t_ = async () => {
  const { diagram: t } = await import("./ganttDiagram-fede5d06.js");
  return { id: Wl, diagram: t };
}, e_ = {
  id: Wl,
  detector: Qy,
  loader: t_
}, i_ = e_, Hl = "info", r_ = (t) => /^\s*info/.test(t), n_ = async () => {
  const { diagram: t } = await import("./infoDiagram-b4f68cb2.js");
  return { id: Hl, diagram: t };
}, o_ = {
  id: Hl,
  detector: r_,
  loader: n_
}, jl = "pie", s_ = (t) => /^\s*pie/.test(t), a_ = async () => {
  const { diagram: t } = await import("./pieDiagram-a0d2778d.js");
  return { id: jl, diagram: t };
}, l_ = {
  id: jl,
  detector: s_,
  loader: a_
}, Ul = "quadrantChart", h_ = (t) => /^\s*quadrantChart/.test(t), c_ = async () => {
  const { diagram: t } = await import("./quadrantDiagram-38c8dfb0.js");
  return { id: Ul, diagram: t };
}, u_ = {
  id: Ul,
  detector: h_,
  loader: c_
}, f_ = u_, Yl = "xychart", d_ = (t) => /^\s*xychart-beta/.test(t), p_ = async () => {
  const { diagram: t } = await import("./xychartDiagram-e4ec1c6f.js");
  return { id: Yl, diagram: t };
}, g_ = {
  id: Yl,
  detector: d_,
  loader: p_
}, m_ = g_, Gl = "requirement", y_ = (t) => /^\s*requirement(Diagram)?/.test(t), __ = async () => {
  const { diagram: t } = await import("./requirementDiagram-fe52b071.js");
  return { id: Gl, diagram: t };
}, C_ = {
  id: Gl,
  detector: y_,
  loader: __
}, x_ = C_, Vl = "sequence", b_ = (t) => /^\s*sequenceDiagram/.test(t), T_ = async () => {
  const { diagram: t } = await import("./sequenceDiagram-76a50a21.js");
  return { id: Vl, diagram: t };
}, S_ = {
  id: Vl,
  detector: b_,
  loader: T_
}, k_ = S_, Xl = "class", v_ = (t, e) => {
  var i;
  return ((i = e == null ? void 0 : e.class) == null ? void 0 : i.defaultRenderer) === "dagre-wrapper" ? !1 : /^\s*classDiagram/.test(t);
}, w_ = async () => {
  const { diagram: t } = await import("./classDiagram-968d359f.js");
  return { id: Xl, diagram: t };
}, B_ = {
  id: Xl,
  detector: v_,
  loader: w_
}, A_ = B_, Kl = "classDiagram", F_ = (t, e) => {
  var i;
  return /^\s*classDiagram/.test(t) && ((i = e == null ? void 0 : e.class) == null ? void 0 : i.defaultRenderer) === "dagre-wrapper" ? !0 : /^\s*classDiagram-v2/.test(t);
}, L_ = async () => {
  const { diagram: t } = await import("./classDiagram-v2-7b5b808d.js");
  return { id: Kl, diagram: t };
}, E_ = {
  id: Kl,
  detector: F_,
  loader: L_
}, O_ = E_, Zl = "state", M_ = (t, e) => {
  var i;
  return ((i = e == null ? void 0 : e.state) == null ? void 0 : i.defaultRenderer) === "dagre-wrapper" ? !1 : /^\s*stateDiagram/.test(t);
}, I_ = async () => {
  const { diagram: t } = await import("./stateDiagram-4a035e10.js");
  return { id: Zl, diagram: t };
}, $_ = {
  id: Zl,
  detector: M_,
  loader: I_
}, D_ = $_, Jl = "stateDiagram", N_ = (t, e) => {
  var i;
  return !!(/^\s*stateDiagram-v2/.test(t) || /^\s*stateDiagram/.test(t) && ((i = e == null ? void 0 : e.state) == null ? void 0 : i.defaultRenderer) === "dagre-wrapper");
}, R_ = async () => {
  const { diagram: t } = await import("./stateDiagram-v2-915d1ba4.js");
  return { id: Jl, diagram: t };
}, P_ = {
  id: Jl,
  detector: N_,
  loader: R_
}, q_ = P_, Ql = "journey", z_ = (t) => /^\s*journey/.test(t), W_ = async () => {
  const { diagram: t } = await import("./journeyDiagram-89a2fe2f.js");
  return { id: Ql, diagram: t };
}, H_ = {
  id: Ql,
  detector: z_,
  loader: W_
}, j_ = H_, U_ = function(t, e) {
  for (let i of e)
    t.attr(i[0], i[1]);
}, Y_ = function(t, e, i) {
  let r = /* @__PURE__ */ new Map();
  return i ? (r.set("width", "100%"), r.set("style", `max-width: ${e}px;`)) : (r.set("height", t), r.set("width", e)), r;
}, th = function(t, e, i, r) {
  const n = Y_(e, i, r);
  U_(t, n);
}, G_ = function(t, e, i, r) {
  const n = e.node().getBBox(), o = n.width, s = n.height;
  A.info(`SVG bounds: ${o}x${s}`, n);
  let a = 0, l = 0;
  A.info(`Graph bounds: ${a}x${l}`, t), a = o + i * 2, l = s + i * 2, A.info(`Calculated bounds: ${a}x${l}`), th(e, l, a, r);
  const h = `${n.x - i} ${n.y - i} ${n.width + 2 * i} ${n.height + 2 * i}`;
  e.attr("viewBox", h);
}, Ki = {};
function Ps(t) {
  return [...t.cssRules].map((e) => e.cssText).join(`
`);
}
const V_ = (t, e, i) => {
  let r = "";
  return t in Ki && Ki[t] ? r = Ki[t](i) : A.warn(`No theme found for ${t}`), ` & {
    font-family: ${i.fontFamily};
    font-size: ${i.fontSize};
    fill: ${i.textColor}
  }

  /* Classes common for multiple diagrams */

  & .error-icon {
    fill: ${i.errorBkgColor};
  }
  & .error-text {
    fill: ${i.errorTextColor};
    stroke: ${i.errorTextColor};
  }

  & .edge-thickness-normal {
    stroke-width: 2px;
  }
  & .edge-thickness-thick {
    stroke-width: 3.5px
  }
  & .edge-pattern-solid {
    stroke-dasharray: 0;
  }

  & .edge-pattern-dashed{
    stroke-dasharray: 3;
  }
  .edge-pattern-dotted {
    stroke-dasharray: 2;
  }

  & .marker {
    fill: ${i.lineColor};
    stroke: ${i.lineColor};
  }
  & .marker.cross {
    stroke: ${i.lineColor};
  }

  & svg {
    font-family: ${i.fontFamily};
    font-size: ${i.fontSize};
  }

  ${r}

  ${e}
`;
}, X_ = (t, e) => {
  e !== void 0 && (Ki[t] = e);
}, K_ = V_;
let so = "", ao = "", lo = "";
const ho = (t) => Pe(t, Ut()), Z_ = () => {
  so = "", lo = "", ao = "";
}, J_ = (t) => {
  so = ho(t).replace(/^\s+/g, "");
}, Q_ = () => so, tC = (t) => {
  lo = ho(t).replace(/\n\s+/g, `
`);
}, eC = () => lo, iC = (t) => {
  ao = ho(t);
}, rC = () => ao, nC = /* @__PURE__ */ Object.freeze(/* @__PURE__ */ Object.defineProperty({
  __proto__: null,
  clear: Z_,
  getAccDescription: eC,
  getAccTitle: Q_,
  getDiagramTitle: rC,
  setAccDescription: tC,
  setAccTitle: J_,
  setDiagramTitle: iC
}, Symbol.toStringTag, { value: "Module" })), oC = A, sC = Dn, co = Ut, K1 = Il, Z1 = We, aC = (t) => Pe(t, co()), lC = G_, hC = () => nC, dr = {}, pr = (t, e, i) => {
  var r;
  if (dr[t])
    throw new Error(`Diagram ${t} already registered.`);
  dr[t] = e, i && sl(t, i), X_(t, e.styles), (r = e.injectUtils) == null || r.call(
    e,
    oC,
    sC,
    co,
    aC,
    lC,
    hC(),
    () => {
    }
  );
}, uo = (t) => {
  if (t in dr)
    return dr[t];
  throw new cC(t);
};
class cC extends Error {
  constructor(e) {
    super(`Diagram ${e} not found.`);
  }
}
const uC = (t) => {
  var n;
  const { securityLevel: e } = co();
  let i = At("body");
  if (e === "sandbox") {
    const s = ((n = At(`#i${t}`).node()) == null ? void 0 : n.contentDocument) ?? document;
    i = At(s.body);
  }
  return i.select(`#${t}`);
}, fC = (t, e, i) => {
  A.debug(`rendering svg for syntax error
`);
  const r = uC(e), n = r.append("g");
  r.attr("viewBox", "0 0 2412 512"), th(r, 100, 512, !0), n.append("path").attr("class", "error-icon").attr(
    "d",
    "m411.313,123.313c6.25-6.25 6.25-16.375 0-22.625s-16.375-6.25-22.625,0l-32,32-9.375,9.375-20.688-20.688c-12.484-12.5-32.766-12.5-45.25,0l-16,16c-1.261,1.261-2.304,2.648-3.31,4.051-21.739-8.561-45.324-13.426-70.065-13.426-105.867,0-192,86.133-192,192s86.133,192 192,192 192-86.133 192-192c0-24.741-4.864-48.327-13.426-70.065 1.402-1.007 2.79-2.049 4.051-3.31l16-16c12.5-12.492 12.5-32.758 0-45.25l-20.688-20.688 9.375-9.375 32.001-31.999zm-219.313,100.687c-52.938,0-96,43.063-96,96 0,8.836-7.164,16-16,16s-16-7.164-16-16c0-70.578 57.422-128 128-128 8.836,0 16,7.164 16,16s-7.164,16-16,16z"
  ), n.append("path").attr("class", "error-icon").attr(
    "d",
    "m459.02,148.98c-6.25-6.25-16.375-6.25-22.625,0s-6.25,16.375 0,22.625l16,16c3.125,3.125 7.219,4.688 11.313,4.688 4.094,0 8.188-1.563 11.313-4.688 6.25-6.25 6.25-16.375 0-22.625l-16.001-16z"
  ), n.append("path").attr("class", "error-icon").attr(
    "d",
    "m340.395,75.605c3.125,3.125 7.219,4.688 11.313,4.688 4.094,0 8.188-1.563 11.313-4.688 6.25-6.25 6.25-16.375 0-22.625l-16-16c-6.25-6.25-16.375-6.25-22.625,0s-6.25,16.375 0,22.625l15.999,16z"
  ), n.append("path").attr("class", "error-icon").attr(
    "d",
    "m400,64c8.844,0 16-7.164 16-16v-32c0-8.836-7.156-16-16-16-8.844,0-16,7.164-16,16v32c0,8.836 7.156,16 16,16z"
  ), n.append("path").attr("class", "error-icon").attr(
    "d",
    "m496,96.586h-32c-8.844,0-16,7.164-16,16 0,8.836 7.156,16 16,16h32c8.844,0 16-7.164 16-16 0-8.836-7.156-16-16-16z"
  ), n.append("path").attr("class", "error-icon").attr(
    "d",
    "m436.98,75.605c3.125,3.125 7.219,4.688 11.313,4.688 4.094,0 8.188-1.563 11.313-4.688l32-32c6.25-6.25 6.25-16.375 0-22.625s-16.375-6.25-22.625,0l-32,32c-6.251,6.25-6.251,16.375-0.001,22.625z"
  ), n.append("text").attr("class", "error-text").attr("x", 1440).attr("y", 250).attr("font-size", "150px").style("text-anchor", "middle").text("Syntax error in text"), n.append("text").attr("class", "error-text").attr("x", 1250).attr("y", 400).attr("font-size", "100px").style("text-anchor", "middle").text(`mermaid version ${i}`);
}, eh = { draw: fC }, dC = eh, pC = {
  db: {},
  renderer: eh,
  parser: {
    parser: { yy: {} },
    parse: () => {
    }
  }
}, gC = pC, ih = "flowchart-elk", mC = (t, e) => {
  var i;
  return (
    // If diagram explicitly states flowchart-elk
    !!(/^\s*flowchart-elk/.test(t) || // If a flowchart/graph diagram has their default renderer set to elk
    /^\s*flowchart|graph/.test(t) && ((i = e == null ? void 0 : e.flowchart) == null ? void 0 : i.defaultRenderer) === "elk")
  );
}, yC = async () => {
  const { diagram: t } = await import("./flowchart-elk-definition-943e3409.js");
  return { id: ih, diagram: t };
}, _C = {
  id: ih,
  detector: mC,
  loader: yC
}, CC = _C, rh = "timeline", xC = (t) => /^\s*timeline/.test(t), bC = async () => {
  const { diagram: t } = await import("./timeline-definition-16881ace.js");
  return { id: rh, diagram: t };
}, TC = {
  id: rh,
  detector: xC,
  loader: bC
}, SC = TC, nh = "mindmap", kC = (t) => /^\s*mindmap/.test(t), vC = async () => {
  const { diagram: t } = await import("./mindmap-definition-ddd6f0cd.js");
  return { id: nh, diagram: t };
}, wC = {
  id: nh,
  detector: kC,
  loader: vC
}, BC = wC, oh = "sankey", AC = (t) => /^\s*sankey-beta/.test(t), FC = async () => {
  const { diagram: t } = await import("./sankeyDiagram-6f81076f.js");
  return { id: oh, diagram: t };
}, LC = {
  id: oh,
  detector: AC,
  loader: FC
}, EC = LC, sh = "block", OC = (t) => /^\s*block-beta/.test(t), MC = async () => {
  const { diagram: t } = await import("./blockDiagram-e7344b80.js");
  return { id: sh, diagram: t };
}, IC = {
  id: sh,
  detector: OC,
  loader: MC
}, $C = IC;
let qs = !1;
const fo = () => {
  qs || (qs = !0, pr("error", gC, (t) => t.toLowerCase().trim() === "error"), pr(
    "---",
    // --- diagram type may appear if YAML front-matter is not parsed correctly
    {
      db: {
        clear: () => {
        }
      },
      styles: {},
      // should never be used
      renderer: {
        draw: () => {
        }
      },
      parser: {
        parser: { yy: {} },
        parse: () => {
          throw new Error(
            "Diagrams beginning with --- are not valid. If you were trying to use a YAML front-matter, please ensure that you've correctly opened and closed the YAML front-matter with un-indented `---` blocks"
          );
        }
      },
      init: () => null
      // no op
    },
    (t) => t.toLowerCase().trimStart().startsWith("---")
  ), ol(
    Dy,
    O_,
    A_,
    Vy,
    i_,
    o_,
    l_,
    x_,
    k_,
    CC,
    jy,
    qy,
    BC,
    SC,
    Jy,
    q_,
    D_,
    j_,
    f_,
    EC,
    m_,
    $C
  ));
};
class ah {
  constructor(e, i = {}) {
    this.text = e, this.metadata = i, this.type = "graph", this.text = hy(e), this.text += `
`;
    const r = Ut();
    try {
      this.type = Br(e, r);
    } catch (o) {
      this.type = "error", this.detectError = o;
    }
    const n = uo(this.type);
    A.debug("Type " + this.type), this.db = n.db, this.renderer = n.renderer, this.parser = n.parser, this.parser.parser.yy = this.db, this.init = n.init, this.parse();
  }
  parse() {
    var i, r, n, o, s;
    if (this.detectError)
      throw this.detectError;
    (r = (i = this.db).clear) == null || r.call(i);
    const e = Ut();
    (n = this.init) == null || n.call(this, e), this.metadata.title && ((s = (o = this.db).setDiagramTitle) == null || s.call(o, this.metadata.title)), this.parser.parse(this.text);
  }
  async render(e, i) {
    await this.renderer.draw(this.text, e, i, this);
  }
  getParser() {
    return this.parser;
  }
  getType() {
    return this.type;
  }
}
const DC = async (t, e = {}) => {
  const i = Br(t, Ut());
  try {
    uo(i);
  } catch {
    const n = jp(i);
    if (!n)
      throw new nl(`Diagram ${i} not found.`);
    const { id: o, diagram: s } = await n();
    pr(o, s);
  }
  return new ah(t, e);
};
let zs = [];
const NC = () => {
  zs.forEach((t) => {
    t();
  }), zs = [];
};
var RC = cl(Object.keys, Object);
const PC = RC;
var qC = Object.prototype, zC = qC.hasOwnProperty;
function WC(t) {
  if (!Er(t))
    return PC(t);
  var e = [];
  for (var i in Object(t))
    zC.call(t, i) && i != "constructor" && e.push(i);
  return e;
}
var HC = ke(Gt, "DataView");
const En = HC;
var jC = ke(Gt, "Promise");
const On = jC;
var UC = ke(Gt, "Set");
const Mn = UC;
var YC = ke(Gt, "WeakMap");
const In = YC;
var Ws = "[object Map]", GC = "[object Object]", Hs = "[object Promise]", js = "[object Set]", Us = "[object WeakMap]", Ys = "[object DataView]", VC = Se(En), XC = Se(_i), KC = Se(On), ZC = Se(Mn), JC = Se(In), pe = Ue;
(En && pe(new En(new ArrayBuffer(1))) != Ys || _i && pe(new _i()) != Ws || On && pe(On.resolve()) != Hs || Mn && pe(new Mn()) != js || In && pe(new In()) != Us) && (pe = function(t) {
  var e = Ue(t), i = e == GC ? t.constructor : void 0, r = i ? Se(i) : "";
  if (r)
    switch (r) {
      case VC:
        return Ys;
      case XC:
        return Ws;
      case KC:
        return Hs;
      case ZC:
        return js;
      case JC:
        return Us;
    }
  return e;
});
const QC = pe;
var tx = "[object Map]", ex = "[object Set]", ix = Object.prototype, rx = ix.hasOwnProperty;
function ln(t) {
  if (t == null)
    return !0;
  if (Or(t) && (cr(t) || typeof t == "string" || typeof t.splice == "function" || Jn(t) || Qn(t) || hr(t)))
    return !t.length;
  var e = QC(t);
  if (e == tx || e == ex)
    return !t.size;
  if (Er(t))
    return !WC(t).length;
  for (var i in t)
    if (rx.call(t, i))
      return !1;
  return !0;
}
const nx = "graphics-document document";
function ox(t, e) {
  t.attr("role", nx), e !== "" && t.attr("aria-roledescription", e);
}
function sx(t, e, i, r) {
  if (t.insert !== void 0) {
    if (i) {
      const n = `chart-desc-${r}`;
      t.attr("aria-describedby", n), t.insert("desc", ":first-child").attr("id", n).text(i);
    }
    if (e) {
      const n = `chart-title-${r}`;
      t.attr("aria-labelledby", n), t.insert("title", ":first-child").attr("id", n).text(e);
    }
  }
}
const ax = (t) => t.replace(/^\s*%%(?!{)[^\n]+\n?/gm, "").trimStart();
/*! js-yaml 4.1.0 https://github.com/nodeca/js-yaml @license MIT */
function lh(t) {
  return typeof t > "u" || t === null;
}
function lx(t) {
  return typeof t == "object" && t !== null;
}
function hx(t) {
  return Array.isArray(t) ? t : lh(t) ? [] : [t];
}
function cx(t, e) {
  var i, r, n, o;
  if (e)
    for (o = Object.keys(e), i = 0, r = o.length; i < r; i += 1)
      n = o[i], t[n] = e[n];
  return t;
}
function ux(t, e) {
  var i = "", r;
  for (r = 0; r < e; r += 1)
    i += t;
  return i;
}
function fx(t) {
  return t === 0 && Number.NEGATIVE_INFINITY === 1 / t;
}
var dx = lh, px = lx, gx = hx, mx = ux, yx = fx, _x = cx, ft = {
  isNothing: dx,
  isObject: px,
  toArray: gx,
  repeat: mx,
  isNegativeZero: yx,
  extend: _x
};
function hh(t, e) {
  var i = "", r = t.reason || "(unknown reason)";
  return t.mark ? (t.mark.name && (i += 'in "' + t.mark.name + '" '), i += "(" + (t.mark.line + 1) + ":" + (t.mark.column + 1) + ")", !e && t.mark.snippet && (i += `

` + t.mark.snippet), r + " " + i) : r;
}
function bi(t, e) {
  Error.call(this), this.name = "YAMLException", this.reason = t, this.mark = e, this.message = hh(this, !1), Error.captureStackTrace ? Error.captureStackTrace(this, this.constructor) : this.stack = new Error().stack || "";
}
bi.prototype = Object.create(Error.prototype);
bi.prototype.constructor = bi;
bi.prototype.toString = function(e) {
  return this.name + ": " + hh(this, e);
};
var Qt = bi;
function hn(t, e, i, r, n) {
  var o = "", s = "", a = Math.floor(n / 2) - 1;
  return r - e > a && (o = " ... ", e = r - a + o.length), i - r > a && (s = " ...", i = r + a - s.length), {
    str: o + t.slice(e, i).replace(/\t/g, "→") + s,
    pos: r - e + o.length
    // relative position
  };
}
function cn(t, e) {
  return ft.repeat(" ", e - t.length) + t;
}
function Cx(t, e) {
  if (e = Object.create(e || null), !t.buffer)
    return null;
  e.maxLength || (e.maxLength = 79), typeof e.indent != "number" && (e.indent = 1), typeof e.linesBefore != "number" && (e.linesBefore = 3), typeof e.linesAfter != "number" && (e.linesAfter = 2);
  for (var i = /\r?\n|\r|\0/g, r = [0], n = [], o, s = -1; o = i.exec(t.buffer); )
    n.push(o.index), r.push(o.index + o[0].length), t.position <= o.index && s < 0 && (s = r.length - 2);
  s < 0 && (s = r.length - 1);
  var a = "", l, h, u = Math.min(t.line + e.linesAfter, n.length).toString().length, d = e.maxLength - (e.indent + u + 3);
  for (l = 1; l <= e.linesBefore && !(s - l < 0); l++)
    h = hn(
      t.buffer,
      r[s - l],
      n[s - l],
      t.position - (r[s] - r[s - l]),
      d
    ), a = ft.repeat(" ", e.indent) + cn((t.line - l + 1).toString(), u) + " | " + h.str + `
` + a;
  for (h = hn(t.buffer, r[s], n[s], t.position, d), a += ft.repeat(" ", e.indent) + cn((t.line + 1).toString(), u) + " | " + h.str + `
`, a += ft.repeat("-", e.indent + u + 3 + h.pos) + `^
`, l = 1; l <= e.linesAfter && !(s + l >= n.length); l++)
    h = hn(
      t.buffer,
      r[s + l],
      n[s + l],
      t.position - (r[s] - r[s + l]),
      d
    ), a += ft.repeat(" ", e.indent) + cn((t.line + l + 1).toString(), u) + " | " + h.str + `
`;
  return a.replace(/\n$/, "");
}
var xx = Cx, bx = [
  "kind",
  "multi",
  "resolve",
  "construct",
  "instanceOf",
  "predicate",
  "represent",
  "representName",
  "defaultStyle",
  "styleAliases"
], Tx = [
  "scalar",
  "sequence",
  "mapping"
];
function Sx(t) {
  var e = {};
  return t !== null && Object.keys(t).forEach(function(i) {
    t[i].forEach(function(r) {
      e[String(r)] = i;
    });
  }), e;
}
function kx(t, e) {
  if (e = e || {}, Object.keys(e).forEach(function(i) {
    if (bx.indexOf(i) === -1)
      throw new Qt('Unknown option "' + i + '" is met in definition of "' + t + '" YAML type.');
  }), this.options = e, this.tag = t, this.kind = e.kind || null, this.resolve = e.resolve || function() {
    return !0;
  }, this.construct = e.construct || function(i) {
    return i;
  }, this.instanceOf = e.instanceOf || null, this.predicate = e.predicate || null, this.represent = e.represent || null, this.representName = e.representName || null, this.defaultStyle = e.defaultStyle || null, this.multi = e.multi || !1, this.styleAliases = Sx(e.styleAliases || null), Tx.indexOf(this.kind) === -1)
    throw new Qt('Unknown kind "' + this.kind + '" is specified for "' + t + '" YAML type.');
}
var ct = kx;
function Gs(t, e) {
  var i = [];
  return t[e].forEach(function(r) {
    var n = i.length;
    i.forEach(function(o, s) {
      o.tag === r.tag && o.kind === r.kind && o.multi === r.multi && (n = s);
    }), i[n] = r;
  }), i;
}
function vx() {
  var t = {
    scalar: {},
    sequence: {},
    mapping: {},
    fallback: {},
    multi: {
      scalar: [],
      sequence: [],
      mapping: [],
      fallback: []
    }
  }, e, i;
  function r(n) {
    n.multi ? (t.multi[n.kind].push(n), t.multi.fallback.push(n)) : t[n.kind][n.tag] = t.fallback[n.tag] = n;
  }
  for (e = 0, i = arguments.length; e < i; e += 1)
    arguments[e].forEach(r);
  return t;
}
function $n(t) {
  return this.extend(t);
}
$n.prototype.extend = function(e) {
  var i = [], r = [];
  if (e instanceof ct)
    r.push(e);
  else if (Array.isArray(e))
    r = r.concat(e);
  else if (e && (Array.isArray(e.implicit) || Array.isArray(e.explicit)))
    e.implicit && (i = i.concat(e.implicit)), e.explicit && (r = r.concat(e.explicit));
  else
    throw new Qt("Schema.extend argument should be a Type, [ Type ], or a schema definition ({ implicit: [...], explicit: [...] })");
  i.forEach(function(o) {
    if (!(o instanceof ct))
      throw new Qt("Specified list of YAML types (or a single Type object) contains a non-Type object.");
    if (o.loadKind && o.loadKind !== "scalar")
      throw new Qt("There is a non-scalar type in the implicit list of a schema. Implicit resolving of such types is not supported.");
    if (o.multi)
      throw new Qt("There is a multi type in the implicit list of a schema. Multi tags can only be listed as explicit.");
  }), r.forEach(function(o) {
    if (!(o instanceof ct))
      throw new Qt("Specified list of YAML types (or a single Type object) contains a non-Type object.");
  });
  var n = Object.create($n.prototype);
  return n.implicit = (this.implicit || []).concat(i), n.explicit = (this.explicit || []).concat(r), n.compiledImplicit = Gs(n, "implicit"), n.compiledExplicit = Gs(n, "explicit"), n.compiledTypeMap = vx(n.compiledImplicit, n.compiledExplicit), n;
};
var wx = $n, Bx = new ct("tag:yaml.org,2002:str", {
  kind: "scalar",
  construct: function(t) {
    return t !== null ? t : "";
  }
}), Ax = new ct("tag:yaml.org,2002:seq", {
  kind: "sequence",
  construct: function(t) {
    return t !== null ? t : [];
  }
}), Fx = new ct("tag:yaml.org,2002:map", {
  kind: "mapping",
  construct: function(t) {
    return t !== null ? t : {};
  }
}), Lx = new wx({
  explicit: [
    Bx,
    Ax,
    Fx
  ]
});
function Ex(t) {
  if (t === null)
    return !0;
  var e = t.length;
  return e === 1 && t === "~" || e === 4 && (t === "null" || t === "Null" || t === "NULL");
}
function Ox() {
  return null;
}
function Mx(t) {
  return t === null;
}
var Ix = new ct("tag:yaml.org,2002:null", {
  kind: "scalar",
  resolve: Ex,
  construct: Ox,
  predicate: Mx,
  represent: {
    canonical: function() {
      return "~";
    },
    lowercase: function() {
      return "null";
    },
    uppercase: function() {
      return "NULL";
    },
    camelcase: function() {
      return "Null";
    },
    empty: function() {
      return "";
    }
  },
  defaultStyle: "lowercase"
});
function $x(t) {
  if (t === null)
    return !1;
  var e = t.length;
  return e === 4 && (t === "true" || t === "True" || t === "TRUE") || e === 5 && (t === "false" || t === "False" || t === "FALSE");
}
function Dx(t) {
  return t === "true" || t === "True" || t === "TRUE";
}
function Nx(t) {
  return Object.prototype.toString.call(t) === "[object Boolean]";
}
var Rx = new ct("tag:yaml.org,2002:bool", {
  kind: "scalar",
  resolve: $x,
  construct: Dx,
  predicate: Nx,
  represent: {
    lowercase: function(t) {
      return t ? "true" : "false";
    },
    uppercase: function(t) {
      return t ? "TRUE" : "FALSE";
    },
    camelcase: function(t) {
      return t ? "True" : "False";
    }
  },
  defaultStyle: "lowercase"
});
function Px(t) {
  return 48 <= t && t <= 57 || 65 <= t && t <= 70 || 97 <= t && t <= 102;
}
function qx(t) {
  return 48 <= t && t <= 55;
}
function zx(t) {
  return 48 <= t && t <= 57;
}
function Wx(t) {
  if (t === null)
    return !1;
  var e = t.length, i = 0, r = !1, n;
  if (!e)
    return !1;
  if (n = t[i], (n === "-" || n === "+") && (n = t[++i]), n === "0") {
    if (i + 1 === e)
      return !0;
    if (n = t[++i], n === "b") {
      for (i++; i < e; i++)
        if (n = t[i], n !== "_") {
          if (n !== "0" && n !== "1")
            return !1;
          r = !0;
        }
      return r && n !== "_";
    }
    if (n === "x") {
      for (i++; i < e; i++)
        if (n = t[i], n !== "_") {
          if (!Px(t.charCodeAt(i)))
            return !1;
          r = !0;
        }
      return r && n !== "_";
    }
    if (n === "o") {
      for (i++; i < e; i++)
        if (n = t[i], n !== "_") {
          if (!qx(t.charCodeAt(i)))
            return !1;
          r = !0;
        }
      return r && n !== "_";
    }
  }
  if (n === "_")
    return !1;
  for (; i < e; i++)
    if (n = t[i], n !== "_") {
      if (!zx(t.charCodeAt(i)))
        return !1;
      r = !0;
    }
  return !(!r || n === "_");
}
function Hx(t) {
  var e = t, i = 1, r;
  if (e.indexOf("_") !== -1 && (e = e.replace(/_/g, "")), r = e[0], (r === "-" || r === "+") && (r === "-" && (i = -1), e = e.slice(1), r = e[0]), e === "0")
    return 0;
  if (r === "0") {
    if (e[1] === "b")
      return i * parseInt(e.slice(2), 2);
    if (e[1] === "x")
      return i * parseInt(e.slice(2), 16);
    if (e[1] === "o")
      return i * parseInt(e.slice(2), 8);
  }
  return i * parseInt(e, 10);
}
function jx(t) {
  return Object.prototype.toString.call(t) === "[object Number]" && t % 1 === 0 && !ft.isNegativeZero(t);
}
var Ux = new ct("tag:yaml.org,2002:int", {
  kind: "scalar",
  resolve: Wx,
  construct: Hx,
  predicate: jx,
  represent: {
    binary: function(t) {
      return t >= 0 ? "0b" + t.toString(2) : "-0b" + t.toString(2).slice(1);
    },
    octal: function(t) {
      return t >= 0 ? "0o" + t.toString(8) : "-0o" + t.toString(8).slice(1);
    },
    decimal: function(t) {
      return t.toString(10);
    },
    /* eslint-disable max-len */
    hexadecimal: function(t) {
      return t >= 0 ? "0x" + t.toString(16).toUpperCase() : "-0x" + t.toString(16).toUpperCase().slice(1);
    }
  },
  defaultStyle: "decimal",
  styleAliases: {
    binary: [2, "bin"],
    octal: [8, "oct"],
    decimal: [10, "dec"],
    hexadecimal: [16, "hex"]
  }
}), Yx = new RegExp(
  // 2.5e4, 2.5 and integers
  "^(?:[-+]?(?:[0-9][0-9_]*)(?:\\.[0-9_]*)?(?:[eE][-+]?[0-9]+)?|\\.[0-9_]+(?:[eE][-+]?[0-9]+)?|[-+]?\\.(?:inf|Inf|INF)|\\.(?:nan|NaN|NAN))$"
);
function Gx(t) {
  return !(t === null || !Yx.test(t) || // Quick hack to not allow integers end with `_`
  // Probably should update regexp & check speed
  t[t.length - 1] === "_");
}
function Vx(t) {
  var e, i;
  return e = t.replace(/_/g, "").toLowerCase(), i = e[0] === "-" ? -1 : 1, "+-".indexOf(e[0]) >= 0 && (e = e.slice(1)), e === ".inf" ? i === 1 ? Number.POSITIVE_INFINITY : Number.NEGATIVE_INFINITY : e === ".nan" ? NaN : i * parseFloat(e, 10);
}
var Xx = /^[-+]?[0-9]+e/;
function Kx(t, e) {
  var i;
  if (isNaN(t))
    switch (e) {
      case "lowercase":
        return ".nan";
      case "uppercase":
        return ".NAN";
      case "camelcase":
        return ".NaN";
    }
  else if (Number.POSITIVE_INFINITY === t)
    switch (e) {
      case "lowercase":
        return ".inf";
      case "uppercase":
        return ".INF";
      case "camelcase":
        return ".Inf";
    }
  else if (Number.NEGATIVE_INFINITY === t)
    switch (e) {
      case "lowercase":
        return "-.inf";
      case "uppercase":
        return "-.INF";
      case "camelcase":
        return "-.Inf";
    }
  else if (ft.isNegativeZero(t))
    return "-0.0";
  return i = t.toString(10), Xx.test(i) ? i.replace("e", ".e") : i;
}
function Zx(t) {
  return Object.prototype.toString.call(t) === "[object Number]" && (t % 1 !== 0 || ft.isNegativeZero(t));
}
var Jx = new ct("tag:yaml.org,2002:float", {
  kind: "scalar",
  resolve: Gx,
  construct: Vx,
  predicate: Zx,
  represent: Kx,
  defaultStyle: "lowercase"
}), ch = Lx.extend({
  implicit: [
    Ix,
    Rx,
    Ux,
    Jx
  ]
}), Qx = ch, uh = new RegExp(
  "^([0-9][0-9][0-9][0-9])-([0-9][0-9])-([0-9][0-9])$"
), fh = new RegExp(
  "^([0-9][0-9][0-9][0-9])-([0-9][0-9]?)-([0-9][0-9]?)(?:[Tt]|[ \\t]+)([0-9][0-9]?):([0-9][0-9]):([0-9][0-9])(?:\\.([0-9]*))?(?:[ \\t]*(Z|([-+])([0-9][0-9]?)(?::([0-9][0-9]))?))?$"
);
function tb(t) {
  return t === null ? !1 : uh.exec(t) !== null || fh.exec(t) !== null;
}
function eb(t) {
  var e, i, r, n, o, s, a, l = 0, h = null, u, d, c;
  if (e = uh.exec(t), e === null && (e = fh.exec(t)), e === null)
    throw new Error("Date resolve error");
  if (i = +e[1], r = +e[2] - 1, n = +e[3], !e[4])
    return new Date(Date.UTC(i, r, n));
  if (o = +e[4], s = +e[5], a = +e[6], e[7]) {
    for (l = e[7].slice(0, 3); l.length < 3; )
      l += "0";
    l = +l;
  }
  return e[9] && (u = +e[10], d = +(e[11] || 0), h = (u * 60 + d) * 6e4, e[9] === "-" && (h = -h)), c = new Date(Date.UTC(i, r, n, o, s, a, l)), h && c.setTime(c.getTime() - h), c;
}
function ib(t) {
  return t.toISOString();
}
var rb = new ct("tag:yaml.org,2002:timestamp", {
  kind: "scalar",
  resolve: tb,
  construct: eb,
  instanceOf: Date,
  represent: ib
});
function nb(t) {
  return t === "<<" || t === null;
}
var ob = new ct("tag:yaml.org,2002:merge", {
  kind: "scalar",
  resolve: nb
}), po = `ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=
\r`;
function sb(t) {
  if (t === null)
    return !1;
  var e, i, r = 0, n = t.length, o = po;
  for (i = 0; i < n; i++)
    if (e = o.indexOf(t.charAt(i)), !(e > 64)) {
      if (e < 0)
        return !1;
      r += 6;
    }
  return r % 8 === 0;
}
function ab(t) {
  var e, i, r = t.replace(/[\r\n=]/g, ""), n = r.length, o = po, s = 0, a = [];
  for (e = 0; e < n; e++)
    e % 4 === 0 && e && (a.push(s >> 16 & 255), a.push(s >> 8 & 255), a.push(s & 255)), s = s << 6 | o.indexOf(r.charAt(e));
  return i = n % 4 * 6, i === 0 ? (a.push(s >> 16 & 255), a.push(s >> 8 & 255), a.push(s & 255)) : i === 18 ? (a.push(s >> 10 & 255), a.push(s >> 2 & 255)) : i === 12 && a.push(s >> 4 & 255), new Uint8Array(a);
}
function lb(t) {
  var e = "", i = 0, r, n, o = t.length, s = po;
  for (r = 0; r < o; r++)
    r % 3 === 0 && r && (e += s[i >> 18 & 63], e += s[i >> 12 & 63], e += s[i >> 6 & 63], e += s[i & 63]), i = (i << 8) + t[r];
  return n = o % 3, n === 0 ? (e += s[i >> 18 & 63], e += s[i >> 12 & 63], e += s[i >> 6 & 63], e += s[i & 63]) : n === 2 ? (e += s[i >> 10 & 63], e += s[i >> 4 & 63], e += s[i << 2 & 63], e += s[64]) : n === 1 && (e += s[i >> 2 & 63], e += s[i << 4 & 63], e += s[64], e += s[64]), e;
}
function hb(t) {
  return Object.prototype.toString.call(t) === "[object Uint8Array]";
}
var cb = new ct("tag:yaml.org,2002:binary", {
  kind: "scalar",
  resolve: sb,
  construct: ab,
  predicate: hb,
  represent: lb
}), ub = Object.prototype.hasOwnProperty, fb = Object.prototype.toString;
function db(t) {
  if (t === null)
    return !0;
  var e = [], i, r, n, o, s, a = t;
  for (i = 0, r = a.length; i < r; i += 1) {
    if (n = a[i], s = !1, fb.call(n) !== "[object Object]")
      return !1;
    for (o in n)
      if (ub.call(n, o))
        if (!s)
          s = !0;
        else
          return !1;
    if (!s)
      return !1;
    if (e.indexOf(o) === -1)
      e.push(o);
    else
      return !1;
  }
  return !0;
}
function pb(t) {
  return t !== null ? t : [];
}
var gb = new ct("tag:yaml.org,2002:omap", {
  kind: "sequence",
  resolve: db,
  construct: pb
}), mb = Object.prototype.toString;
function yb(t) {
  if (t === null)
    return !0;
  var e, i, r, n, o, s = t;
  for (o = new Array(s.length), e = 0, i = s.length; e < i; e += 1) {
    if (r = s[e], mb.call(r) !== "[object Object]" || (n = Object.keys(r), n.length !== 1))
      return !1;
    o[e] = [n[0], r[n[0]]];
  }
  return !0;
}
function _b(t) {
  if (t === null)
    return [];
  var e, i, r, n, o, s = t;
  for (o = new Array(s.length), e = 0, i = s.length; e < i; e += 1)
    r = s[e], n = Object.keys(r), o[e] = [n[0], r[n[0]]];
  return o;
}
var Cb = new ct("tag:yaml.org,2002:pairs", {
  kind: "sequence",
  resolve: yb,
  construct: _b
}), xb = Object.prototype.hasOwnProperty;
function bb(t) {
  if (t === null)
    return !0;
  var e, i = t;
  for (e in i)
    if (xb.call(i, e) && i[e] !== null)
      return !1;
  return !0;
}
function Tb(t) {
  return t !== null ? t : {};
}
var Sb = new ct("tag:yaml.org,2002:set", {
  kind: "mapping",
  resolve: bb,
  construct: Tb
}), kb = Qx.extend({
  implicit: [
    rb,
    ob
  ],
  explicit: [
    cb,
    gb,
    Cb,
    Sb
  ]
}), he = Object.prototype.hasOwnProperty, gr = 1, dh = 2, ph = 3, mr = 4, un = 1, vb = 2, Vs = 3, wb = /[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x84\x86-\x9F\uFFFE\uFFFF]|[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?:[^\uD800-\uDBFF]|^)[\uDC00-\uDFFF]/, Bb = /[\x85\u2028\u2029]/, Ab = /[,\[\]\{\}]/, gh = /^(?:!|!!|![a-z\-]+!)$/i, mh = /^(?:!|[^,\[\]\{\}])(?:%[0-9a-f]{2}|[0-9a-z\-#;\/\?:@&=\+\$,_\.!~\*'\(\)\[\]])*$/i;
function Xs(t) {
  return Object.prototype.toString.call(t);
}
function Ht(t) {
  return t === 10 || t === 13;
}
function _e(t) {
  return t === 9 || t === 32;
}
function xt(t) {
  return t === 9 || t === 32 || t === 10 || t === 13;
}
function Me(t) {
  return t === 44 || t === 91 || t === 93 || t === 123 || t === 125;
}
function Fb(t) {
  var e;
  return 48 <= t && t <= 57 ? t - 48 : (e = t | 32, 97 <= e && e <= 102 ? e - 97 + 10 : -1);
}
function Lb(t) {
  return t === 120 ? 2 : t === 117 ? 4 : t === 85 ? 8 : 0;
}
function Eb(t) {
  return 48 <= t && t <= 57 ? t - 48 : -1;
}
function Ks(t) {
  return t === 48 ? "\0" : t === 97 ? "\x07" : t === 98 ? "\b" : t === 116 || t === 9 ? "	" : t === 110 ? `
` : t === 118 ? "\v" : t === 102 ? "\f" : t === 114 ? "\r" : t === 101 ? "\x1B" : t === 32 ? " " : t === 34 ? '"' : t === 47 ? "/" : t === 92 ? "\\" : t === 78 ? "" : t === 95 ? " " : t === 76 ? "\u2028" : t === 80 ? "\u2029" : "";
}
function Ob(t) {
  return t <= 65535 ? String.fromCharCode(t) : String.fromCharCode(
    (t - 65536 >> 10) + 55296,
    (t - 65536 & 1023) + 56320
  );
}
var yh = new Array(256), _h = new Array(256);
for (var Le = 0; Le < 256; Le++)
  yh[Le] = Ks(Le) ? 1 : 0, _h[Le] = Ks(Le);
function Mb(t, e) {
  this.input = t, this.filename = e.filename || null, this.schema = e.schema || kb, this.onWarning = e.onWarning || null, this.legacy = e.legacy || !1, this.json = e.json || !1, this.listener = e.listener || null, this.implicitTypes = this.schema.compiledImplicit, this.typeMap = this.schema.compiledTypeMap, this.length = t.length, this.position = 0, this.line = 0, this.lineStart = 0, this.lineIndent = 0, this.firstTabInLine = -1, this.documents = [];
}
function Ch(t, e) {
  var i = {
    name: t.filename,
    buffer: t.input.slice(0, -1),
    // omit trailing \0
    position: t.position,
    line: t.line,
    column: t.position - t.lineStart
  };
  return i.snippet = xx(i), new Qt(e, i);
}
function B(t, e) {
  throw Ch(t, e);
}
function yr(t, e) {
  t.onWarning && t.onWarning.call(null, Ch(t, e));
}
var Zs = {
  YAML: function(e, i, r) {
    var n, o, s;
    e.version !== null && B(e, "duplication of %YAML directive"), r.length !== 1 && B(e, "YAML directive accepts exactly one argument"), n = /^([0-9]+)\.([0-9]+)$/.exec(r[0]), n === null && B(e, "ill-formed argument of the YAML directive"), o = parseInt(n[1], 10), s = parseInt(n[2], 10), o !== 1 && B(e, "unacceptable YAML version of the document"), e.version = r[0], e.checkLineBreaks = s < 2, s !== 1 && s !== 2 && yr(e, "unsupported YAML version of the document");
  },
  TAG: function(e, i, r) {
    var n, o;
    r.length !== 2 && B(e, "TAG directive accepts exactly two arguments"), n = r[0], o = r[1], gh.test(n) || B(e, "ill-formed tag handle (first argument) of the TAG directive"), he.call(e.tagMap, n) && B(e, 'there is a previously declared suffix for "' + n + '" tag handle'), mh.test(o) || B(e, "ill-formed tag prefix (second argument) of the TAG directive");
    try {
      o = decodeURIComponent(o);
    } catch {
      B(e, "tag prefix is malformed: " + o);
    }
    e.tagMap[n] = o;
  }
};
function ae(t, e, i, r) {
  var n, o, s, a;
  if (e < i) {
    if (a = t.input.slice(e, i), r)
      for (n = 0, o = a.length; n < o; n += 1)
        s = a.charCodeAt(n), s === 9 || 32 <= s && s <= 1114111 || B(t, "expected valid JSON character");
    else
      wb.test(a) && B(t, "the stream contains non-printable characters");
    t.result += a;
  }
}
function Js(t, e, i, r) {
  var n, o, s, a;
  for (ft.isObject(i) || B(t, "cannot merge mappings; the provided source object is unacceptable"), n = Object.keys(i), s = 0, a = n.length; s < a; s += 1)
    o = n[s], he.call(e, o) || (e[o] = i[o], r[o] = !0);
}
function Ie(t, e, i, r, n, o, s, a, l) {
  var h, u;
  if (Array.isArray(n))
    for (n = Array.prototype.slice.call(n), h = 0, u = n.length; h < u; h += 1)
      Array.isArray(n[h]) && B(t, "nested arrays are not supported inside keys"), typeof n == "object" && Xs(n[h]) === "[object Object]" && (n[h] = "[object Object]");
  if (typeof n == "object" && Xs(n) === "[object Object]" && (n = "[object Object]"), n = String(n), e === null && (e = {}), r === "tag:yaml.org,2002:merge")
    if (Array.isArray(o))
      for (h = 0, u = o.length; h < u; h += 1)
        Js(t, e, o[h], i);
    else
      Js(t, e, o, i);
  else
    !t.json && !he.call(i, n) && he.call(e, n) && (t.line = s || t.line, t.lineStart = a || t.lineStart, t.position = l || t.position, B(t, "duplicated mapping key")), n === "__proto__" ? Object.defineProperty(e, n, {
      configurable: !0,
      enumerable: !0,
      writable: !0,
      value: o
    }) : e[n] = o, delete i[n];
  return e;
}
function go(t) {
  var e;
  e = t.input.charCodeAt(t.position), e === 10 ? t.position++ : e === 13 ? (t.position++, t.input.charCodeAt(t.position) === 10 && t.position++) : B(t, "a line break is expected"), t.line += 1, t.lineStart = t.position, t.firstTabInLine = -1;
}
function tt(t, e, i) {
  for (var r = 0, n = t.input.charCodeAt(t.position); n !== 0; ) {
    for (; _e(n); )
      n === 9 && t.firstTabInLine === -1 && (t.firstTabInLine = t.position), n = t.input.charCodeAt(++t.position);
    if (e && n === 35)
      do
        n = t.input.charCodeAt(++t.position);
      while (n !== 10 && n !== 13 && n !== 0);
    if (Ht(n))
      for (go(t), n = t.input.charCodeAt(t.position), r++, t.lineIndent = 0; n === 32; )
        t.lineIndent++, n = t.input.charCodeAt(++t.position);
    else
      break;
  }
  return i !== -1 && r !== 0 && t.lineIndent < i && yr(t, "deficient indentation"), r;
}
function Dr(t) {
  var e = t.position, i;
  return i = t.input.charCodeAt(e), !!((i === 45 || i === 46) && i === t.input.charCodeAt(e + 1) && i === t.input.charCodeAt(e + 2) && (e += 3, i = t.input.charCodeAt(e), i === 0 || xt(i)));
}
function mo(t, e) {
  e === 1 ? t.result += " " : e > 1 && (t.result += ft.repeat(`
`, e - 1));
}
function Ib(t, e, i) {
  var r, n, o, s, a, l, h, u, d = t.kind, c = t.result, p;
  if (p = t.input.charCodeAt(t.position), xt(p) || Me(p) || p === 35 || p === 38 || p === 42 || p === 33 || p === 124 || p === 62 || p === 39 || p === 34 || p === 37 || p === 64 || p === 96 || (p === 63 || p === 45) && (n = t.input.charCodeAt(t.position + 1), xt(n) || i && Me(n)))
    return !1;
  for (t.kind = "scalar", t.result = "", o = s = t.position, a = !1; p !== 0; ) {
    if (p === 58) {
      if (n = t.input.charCodeAt(t.position + 1), xt(n) || i && Me(n))
        break;
    } else if (p === 35) {
      if (r = t.input.charCodeAt(t.position - 1), xt(r))
        break;
    } else {
      if (t.position === t.lineStart && Dr(t) || i && Me(p))
        break;
      if (Ht(p))
        if (l = t.line, h = t.lineStart, u = t.lineIndent, tt(t, !1, -1), t.lineIndent >= e) {
          a = !0, p = t.input.charCodeAt(t.position);
          continue;
        } else {
          t.position = s, t.line = l, t.lineStart = h, t.lineIndent = u;
          break;
        }
    }
    a && (ae(t, o, s, !1), mo(t, t.line - l), o = s = t.position, a = !1), _e(p) || (s = t.position + 1), p = t.input.charCodeAt(++t.position);
  }
  return ae(t, o, s, !1), t.result ? !0 : (t.kind = d, t.result = c, !1);
}
function $b(t, e) {
  var i, r, n;
  if (i = t.input.charCodeAt(t.position), i !== 39)
    return !1;
  for (t.kind = "scalar", t.result = "", t.position++, r = n = t.position; (i = t.input.charCodeAt(t.position)) !== 0; )
    if (i === 39)
      if (ae(t, r, t.position, !0), i = t.input.charCodeAt(++t.position), i === 39)
        r = t.position, t.position++, n = t.position;
      else
        return !0;
    else
      Ht(i) ? (ae(t, r, n, !0), mo(t, tt(t, !1, e)), r = n = t.position) : t.position === t.lineStart && Dr(t) ? B(t, "unexpected end of the document within a single quoted scalar") : (t.position++, n = t.position);
  B(t, "unexpected end of the stream within a single quoted scalar");
}
function Db(t, e) {
  var i, r, n, o, s, a;
  if (a = t.input.charCodeAt(t.position), a !== 34)
    return !1;
  for (t.kind = "scalar", t.result = "", t.position++, i = r = t.position; (a = t.input.charCodeAt(t.position)) !== 0; ) {
    if (a === 34)
      return ae(t, i, t.position, !0), t.position++, !0;
    if (a === 92) {
      if (ae(t, i, t.position, !0), a = t.input.charCodeAt(++t.position), Ht(a))
        tt(t, !1, e);
      else if (a < 256 && yh[a])
        t.result += _h[a], t.position++;
      else if ((s = Lb(a)) > 0) {
        for (n = s, o = 0; n > 0; n--)
          a = t.input.charCodeAt(++t.position), (s = Fb(a)) >= 0 ? o = (o << 4) + s : B(t, "expected hexadecimal character");
        t.result += Ob(o), t.position++;
      } else
        B(t, "unknown escape sequence");
      i = r = t.position;
    } else
      Ht(a) ? (ae(t, i, r, !0), mo(t, tt(t, !1, e)), i = r = t.position) : t.position === t.lineStart && Dr(t) ? B(t, "unexpected end of the document within a double quoted scalar") : (t.position++, r = t.position);
  }
  B(t, "unexpected end of the stream within a double quoted scalar");
}
function Nb(t, e) {
  var i = !0, r, n, o, s = t.tag, a, l = t.anchor, h, u, d, c, p, y = /* @__PURE__ */ Object.create(null), S, M, q, T;
  if (T = t.input.charCodeAt(t.position), T === 91)
    u = 93, p = !1, a = [];
  else if (T === 123)
    u = 125, p = !0, a = {};
  else
    return !1;
  for (t.anchor !== null && (t.anchorMap[t.anchor] = a), T = t.input.charCodeAt(++t.position); T !== 0; ) {
    if (tt(t, !0, e), T = t.input.charCodeAt(t.position), T === u)
      return t.position++, t.tag = s, t.anchor = l, t.kind = p ? "mapping" : "sequence", t.result = a, !0;
    i ? T === 44 && B(t, "expected the node content, but found ','") : B(t, "missed comma between flow collection entries"), M = S = q = null, d = c = !1, T === 63 && (h = t.input.charCodeAt(t.position + 1), xt(h) && (d = c = !0, t.position++, tt(t, !0, e))), r = t.line, n = t.lineStart, o = t.position, je(t, e, gr, !1, !0), M = t.tag, S = t.result, tt(t, !0, e), T = t.input.charCodeAt(t.position), (c || t.line === r) && T === 58 && (d = !0, T = t.input.charCodeAt(++t.position), tt(t, !0, e), je(t, e, gr, !1, !0), q = t.result), p ? Ie(t, a, y, M, S, q, r, n, o) : d ? a.push(Ie(t, null, y, M, S, q, r, n, o)) : a.push(S), tt(t, !0, e), T = t.input.charCodeAt(t.position), T === 44 ? (i = !0, T = t.input.charCodeAt(++t.position)) : i = !1;
  }
  B(t, "unexpected end of the stream within a flow collection");
}
function Rb(t, e) {
  var i, r, n = un, o = !1, s = !1, a = e, l = 0, h = !1, u, d;
  if (d = t.input.charCodeAt(t.position), d === 124)
    r = !1;
  else if (d === 62)
    r = !0;
  else
    return !1;
  for (t.kind = "scalar", t.result = ""; d !== 0; )
    if (d = t.input.charCodeAt(++t.position), d === 43 || d === 45)
      un === n ? n = d === 43 ? Vs : vb : B(t, "repeat of a chomping mode identifier");
    else if ((u = Eb(d)) >= 0)
      u === 0 ? B(t, "bad explicit indentation width of a block scalar; it cannot be less than one") : s ? B(t, "repeat of an indentation width identifier") : (a = e + u - 1, s = !0);
    else
      break;
  if (_e(d)) {
    do
      d = t.input.charCodeAt(++t.position);
    while (_e(d));
    if (d === 35)
      do
        d = t.input.charCodeAt(++t.position);
      while (!Ht(d) && d !== 0);
  }
  for (; d !== 0; ) {
    for (go(t), t.lineIndent = 0, d = t.input.charCodeAt(t.position); (!s || t.lineIndent < a) && d === 32; )
      t.lineIndent++, d = t.input.charCodeAt(++t.position);
    if (!s && t.lineIndent > a && (a = t.lineIndent), Ht(d)) {
      l++;
      continue;
    }
    if (t.lineIndent < a) {
      n === Vs ? t.result += ft.repeat(`
`, o ? 1 + l : l) : n === un && o && (t.result += `
`);
      break;
    }
    for (r ? _e(d) ? (h = !0, t.result += ft.repeat(`
`, o ? 1 + l : l)) : h ? (h = !1, t.result += ft.repeat(`
`, l + 1)) : l === 0 ? o && (t.result += " ") : t.result += ft.repeat(`
`, l) : t.result += ft.repeat(`
`, o ? 1 + l : l), o = !0, s = !0, l = 0, i = t.position; !Ht(d) && d !== 0; )
      d = t.input.charCodeAt(++t.position);
    ae(t, i, t.position, !1);
  }
  return !0;
}
function Qs(t, e) {
  var i, r = t.tag, n = t.anchor, o = [], s, a = !1, l;
  if (t.firstTabInLine !== -1)
    return !1;
  for (t.anchor !== null && (t.anchorMap[t.anchor] = o), l = t.input.charCodeAt(t.position); l !== 0 && (t.firstTabInLine !== -1 && (t.position = t.firstTabInLine, B(t, "tab characters must not be used in indentation")), !(l !== 45 || (s = t.input.charCodeAt(t.position + 1), !xt(s)))); ) {
    if (a = !0, t.position++, tt(t, !0, -1) && t.lineIndent <= e) {
      o.push(null), l = t.input.charCodeAt(t.position);
      continue;
    }
    if (i = t.line, je(t, e, ph, !1, !0), o.push(t.result), tt(t, !0, -1), l = t.input.charCodeAt(t.position), (t.line === i || t.lineIndent > e) && l !== 0)
      B(t, "bad indentation of a sequence entry");
    else if (t.lineIndent < e)
      break;
  }
  return a ? (t.tag = r, t.anchor = n, t.kind = "sequence", t.result = o, !0) : !1;
}
function Pb(t, e, i) {
  var r, n, o, s, a, l, h = t.tag, u = t.anchor, d = {}, c = /* @__PURE__ */ Object.create(null), p = null, y = null, S = null, M = !1, q = !1, T;
  if (t.firstTabInLine !== -1)
    return !1;
  for (t.anchor !== null && (t.anchorMap[t.anchor] = d), T = t.input.charCodeAt(t.position); T !== 0; ) {
    if (!M && t.firstTabInLine !== -1 && (t.position = t.firstTabInLine, B(t, "tab characters must not be used in indentation")), r = t.input.charCodeAt(t.position + 1), o = t.line, (T === 63 || T === 58) && xt(r))
      T === 63 ? (M && (Ie(t, d, c, p, y, null, s, a, l), p = y = S = null), q = !0, M = !0, n = !0) : M ? (M = !1, n = !0) : B(t, "incomplete explicit mapping pair; a key node is missed; or followed by a non-tabulated empty line"), t.position += 1, T = r;
    else {
      if (s = t.line, a = t.lineStart, l = t.position, !je(t, i, dh, !1, !0))
        break;
      if (t.line === o) {
        for (T = t.input.charCodeAt(t.position); _e(T); )
          T = t.input.charCodeAt(++t.position);
        if (T === 58)
          T = t.input.charCodeAt(++t.position), xt(T) || B(t, "a whitespace character is expected after the key-value separator within a block mapping"), M && (Ie(t, d, c, p, y, null, s, a, l), p = y = S = null), q = !0, M = !1, n = !1, p = t.tag, y = t.result;
        else if (q)
          B(t, "can not read an implicit mapping pair; a colon is missed");
        else
          return t.tag = h, t.anchor = u, !0;
      } else if (q)
        B(t, "can not read a block mapping entry; a multiline key may not be an implicit key");
      else
        return t.tag = h, t.anchor = u, !0;
    }
    if ((t.line === o || t.lineIndent > e) && (M && (s = t.line, a = t.lineStart, l = t.position), je(t, e, mr, !0, n) && (M ? y = t.result : S = t.result), M || (Ie(t, d, c, p, y, S, s, a, l), p = y = S = null), tt(t, !0, -1), T = t.input.charCodeAt(t.position)), (t.line === o || t.lineIndent > e) && T !== 0)
      B(t, "bad indentation of a mapping entry");
    else if (t.lineIndent < e)
      break;
  }
  return M && Ie(t, d, c, p, y, null, s, a, l), q && (t.tag = h, t.anchor = u, t.kind = "mapping", t.result = d), q;
}
function qb(t) {
  var e, i = !1, r = !1, n, o, s;
  if (s = t.input.charCodeAt(t.position), s !== 33)
    return !1;
  if (t.tag !== null && B(t, "duplication of a tag property"), s = t.input.charCodeAt(++t.position), s === 60 ? (i = !0, s = t.input.charCodeAt(++t.position)) : s === 33 ? (r = !0, n = "!!", s = t.input.charCodeAt(++t.position)) : n = "!", e = t.position, i) {
    do
      s = t.input.charCodeAt(++t.position);
    while (s !== 0 && s !== 62);
    t.position < t.length ? (o = t.input.slice(e, t.position), s = t.input.charCodeAt(++t.position)) : B(t, "unexpected end of the stream within a verbatim tag");
  } else {
    for (; s !== 0 && !xt(s); )
      s === 33 && (r ? B(t, "tag suffix cannot contain exclamation marks") : (n = t.input.slice(e - 1, t.position + 1), gh.test(n) || B(t, "named tag handle cannot contain such characters"), r = !0, e = t.position + 1)), s = t.input.charCodeAt(++t.position);
    o = t.input.slice(e, t.position), Ab.test(o) && B(t, "tag suffix cannot contain flow indicator characters");
  }
  o && !mh.test(o) && B(t, "tag name cannot contain such characters: " + o);
  try {
    o = decodeURIComponent(o);
  } catch {
    B(t, "tag name is malformed: " + o);
  }
  return i ? t.tag = o : he.call(t.tagMap, n) ? t.tag = t.tagMap[n] + o : n === "!" ? t.tag = "!" + o : n === "!!" ? t.tag = "tag:yaml.org,2002:" + o : B(t, 'undeclared tag handle "' + n + '"'), !0;
}
function zb(t) {
  var e, i;
  if (i = t.input.charCodeAt(t.position), i !== 38)
    return !1;
  for (t.anchor !== null && B(t, "duplication of an anchor property"), i = t.input.charCodeAt(++t.position), e = t.position; i !== 0 && !xt(i) && !Me(i); )
    i = t.input.charCodeAt(++t.position);
  return t.position === e && B(t, "name of an anchor node must contain at least one character"), t.anchor = t.input.slice(e, t.position), !0;
}
function Wb(t) {
  var e, i, r;
  if (r = t.input.charCodeAt(t.position), r !== 42)
    return !1;
  for (r = t.input.charCodeAt(++t.position), e = t.position; r !== 0 && !xt(r) && !Me(r); )
    r = t.input.charCodeAt(++t.position);
  return t.position === e && B(t, "name of an alias node must contain at least one character"), i = t.input.slice(e, t.position), he.call(t.anchorMap, i) || B(t, 'unidentified alias "' + i + '"'), t.result = t.anchorMap[i], tt(t, !0, -1), !0;
}
function je(t, e, i, r, n) {
  var o, s, a, l = 1, h = !1, u = !1, d, c, p, y, S, M;
  if (t.listener !== null && t.listener("open", t), t.tag = null, t.anchor = null, t.kind = null, t.result = null, o = s = a = mr === i || ph === i, r && tt(t, !0, -1) && (h = !0, t.lineIndent > e ? l = 1 : t.lineIndent === e ? l = 0 : t.lineIndent < e && (l = -1)), l === 1)
    for (; qb(t) || zb(t); )
      tt(t, !0, -1) ? (h = !0, a = o, t.lineIndent > e ? l = 1 : t.lineIndent === e ? l = 0 : t.lineIndent < e && (l = -1)) : a = !1;
  if (a && (a = h || n), (l === 1 || mr === i) && (gr === i || dh === i ? S = e : S = e + 1, M = t.position - t.lineStart, l === 1 ? a && (Qs(t, M) || Pb(t, M, S)) || Nb(t, S) ? u = !0 : (s && Rb(t, S) || $b(t, S) || Db(t, S) ? u = !0 : Wb(t) ? (u = !0, (t.tag !== null || t.anchor !== null) && B(t, "alias node should not have any properties")) : Ib(t, S, gr === i) && (u = !0, t.tag === null && (t.tag = "?")), t.anchor !== null && (t.anchorMap[t.anchor] = t.result)) : l === 0 && (u = a && Qs(t, M))), t.tag === null)
    t.anchor !== null && (t.anchorMap[t.anchor] = t.result);
  else if (t.tag === "?") {
    for (t.result !== null && t.kind !== "scalar" && B(t, 'unacceptable node kind for !<?> tag; it should be "scalar", not "' + t.kind + '"'), d = 0, c = t.implicitTypes.length; d < c; d += 1)
      if (y = t.implicitTypes[d], y.resolve(t.result)) {
        t.result = y.construct(t.result), t.tag = y.tag, t.anchor !== null && (t.anchorMap[t.anchor] = t.result);
        break;
      }
  } else if (t.tag !== "!") {
    if (he.call(t.typeMap[t.kind || "fallback"], t.tag))
      y = t.typeMap[t.kind || "fallback"][t.tag];
    else
      for (y = null, p = t.typeMap.multi[t.kind || "fallback"], d = 0, c = p.length; d < c; d += 1)
        if (t.tag.slice(0, p[d].tag.length) === p[d].tag) {
          y = p[d];
          break;
        }
    y || B(t, "unknown tag !<" + t.tag + ">"), t.result !== null && y.kind !== t.kind && B(t, "unacceptable node kind for !<" + t.tag + '> tag; it should be "' + y.kind + '", not "' + t.kind + '"'), y.resolve(t.result, t.tag) ? (t.result = y.construct(t.result, t.tag), t.anchor !== null && (t.anchorMap[t.anchor] = t.result)) : B(t, "cannot resolve a node with !<" + t.tag + "> explicit tag");
  }
  return t.listener !== null && t.listener("close", t), t.tag !== null || t.anchor !== null || u;
}
function Hb(t) {
  var e = t.position, i, r, n, o = !1, s;
  for (t.version = null, t.checkLineBreaks = t.legacy, t.tagMap = /* @__PURE__ */ Object.create(null), t.anchorMap = /* @__PURE__ */ Object.create(null); (s = t.input.charCodeAt(t.position)) !== 0 && (tt(t, !0, -1), s = t.input.charCodeAt(t.position), !(t.lineIndent > 0 || s !== 37)); ) {
    for (o = !0, s = t.input.charCodeAt(++t.position), i = t.position; s !== 0 && !xt(s); )
      s = t.input.charCodeAt(++t.position);
    for (r = t.input.slice(i, t.position), n = [], r.length < 1 && B(t, "directive name must not be less than one character in length"); s !== 0; ) {
      for (; _e(s); )
        s = t.input.charCodeAt(++t.position);
      if (s === 35) {
        do
          s = t.input.charCodeAt(++t.position);
        while (s !== 0 && !Ht(s));
        break;
      }
      if (Ht(s))
        break;
      for (i = t.position; s !== 0 && !xt(s); )
        s = t.input.charCodeAt(++t.position);
      n.push(t.input.slice(i, t.position));
    }
    s !== 0 && go(t), he.call(Zs, r) ? Zs[r](t, r, n) : yr(t, 'unknown document directive "' + r + '"');
  }
  if (tt(t, !0, -1), t.lineIndent === 0 && t.input.charCodeAt(t.position) === 45 && t.input.charCodeAt(t.position + 1) === 45 && t.input.charCodeAt(t.position + 2) === 45 ? (t.position += 3, tt(t, !0, -1)) : o && B(t, "directives end mark is expected"), je(t, t.lineIndent - 1, mr, !1, !0), tt(t, !0, -1), t.checkLineBreaks && Bb.test(t.input.slice(e, t.position)) && yr(t, "non-ASCII line breaks are interpreted as content"), t.documents.push(t.result), t.position === t.lineStart && Dr(t)) {
    t.input.charCodeAt(t.position) === 46 && (t.position += 3, tt(t, !0, -1));
    return;
  }
  if (t.position < t.length - 1)
    B(t, "end of the stream or a document separator is expected");
  else
    return;
}
function xh(t, e) {
  t = String(t), e = e || {}, t.length !== 0 && (t.charCodeAt(t.length - 1) !== 10 && t.charCodeAt(t.length - 1) !== 13 && (t += `
`), t.charCodeAt(0) === 65279 && (t = t.slice(1)));
  var i = new Mb(t, e), r = t.indexOf("\0");
  for (r !== -1 && (i.position = r, B(i, "null byte is not allowed in input")), i.input += "\0"; i.input.charCodeAt(i.position) === 32; )
    i.lineIndent += 1, i.position += 1;
  for (; i.position < i.length - 1; )
    Hb(i);
  return i.documents;
}
function jb(t, e, i) {
  e !== null && typeof e == "object" && typeof i > "u" && (i = e, e = null);
  var r = xh(t, i);
  if (typeof e != "function")
    return r;
  for (var n = 0, o = r.length; n < o; n += 1)
    e(r[n]);
}
function Ub(t, e) {
  var i = xh(t, e);
  if (i.length !== 0) {
    if (i.length === 1)
      return i[0];
    throw new Qt("expected a single document in the stream, but found more");
  }
}
var Yb = jb, Gb = Ub, Vb = {
  loadAll: Yb,
  load: Gb
}, Xb = ch, Kb = Vb.load;
function Zb(t) {
  const e = t.match(rl);
  if (!e)
    return {
      text: t,
      metadata: {}
    };
  let i = Kb(e[1], {
    // To support config, we need JSON schema.
    // https://www.yaml.org/spec/1.2/spec.html#id2803231
    schema: Xb
  }) ?? {};
  i = typeof i == "object" && !Array.isArray(i) ? i : {};
  const r = {};
  return i.displayMode && (r.displayMode = i.displayMode.toString()), i.title && (r.title = i.title.toString()), i.config && (r.config = i.config), {
    text: t.slice(e[0].length),
    metadata: r
  };
}
const Jb = (t) => t.replace(/\r\n?/g, `
`).replace(
  /<(\w+)([^>]*)>/g,
  (e, i, r) => "<" + i + r.replace(/="([^"]*)"/g, "='$1'") + ">"
), Qb = (t) => {
  const { text: e, metadata: i } = Zb(t), { displayMode: r, title: n, config: o = {} } = i;
  return r && (o.gantt || (o.gantt = {}), o.gantt.displayMode = r), { title: n, config: o, text: e };
}, t1 = (t) => {
  const e = ui.detectInit(t) ?? {}, i = ui.detectDirective(t, "wrap");
  return Array.isArray(i) ? e.wrap = i.some(({ type: r }) => {
  }) : (i == null ? void 0 : i.type) === "wrap" && (e.wrap = !0), {
    text: W0(t),
    directive: e
  };
};
function bh(t) {
  const e = Jb(t), i = Qb(e), r = t1(i.text), n = kl(i.config, r.directive);
  return t = ax(r.text), {
    code: t,
    title: i.title,
    config: n
  };
}
const e1 = 5e4, i1 = "graph TB;a[Maximum text size in diagram exceeded];style a fill:#faa", r1 = "sandbox", n1 = "loose", o1 = "http://www.w3.org/2000/svg", s1 = "http://www.w3.org/1999/xlink", a1 = "http://www.w3.org/1999/xhtml", l1 = "100%", h1 = "100%", c1 = "border:0;margin:0;", u1 = "margin:0", f1 = "allow-top-navigation-by-user-activation allow-popups", d1 = 'The "iframe" tag is not supported by your browser.', p1 = ["foreignobject"], g1 = ["dominant-baseline"];
function Th(t) {
  const e = bh(t);
  return fr(), Ly(e.config ?? {}), e;
}
async function m1(t, e) {
  fo(), t = Th(t).code;
  try {
    await yo(t);
  } catch (i) {
    if (e != null && e.suppressErrors)
      return !1;
    throw i;
  }
  return !0;
}
const ta = (t, e, i = []) => {
  const r = il(`{ ${i.join(" !important; ")} !important; }`);
  return `.${t} ${e} ${r}`;
}, y1 = (t, e = {}) => {
  var n;
  const i = new CSSStyleSheet();
  if (t.fontFamily !== void 0 && i.insertRule(
    `:root { --mermaid-font-family: ${t.fontFamily}}`,
    i.cssRules.length
  ), t.altFontFamily !== void 0 && i.insertRule(
    `:root { --mermaid-alt-font-family: ${t.altFontFamily}}`,
    i.cssRules.length
  ), !ln(e)) {
    const l = t.htmlLabels || ((n = t.flowchart) == null ? void 0 : n.htmlLabels) ? ["> *", "span"] : ["rect", "polygon", "ellipse", "circle", "path"];
    for (const h in e) {
      const u = e[h];
      ln(u.styles) || l.forEach((d) => {
        i.insertRule(
          ta(u.id, d, u.styles),
          i.cssRules.length
        );
      }), ln(u.textStyles) || i.insertRule(
        ta(u.id, "tspan", u.textStyles),
        i.cssRules.length
      );
    }
  }
  let r = "";
  if (t.themeCSS !== void 0)
    if (typeof i.replaceSync == "function") {
      const o = new CSSStyleSheet();
      o.replaceSync(t.themeCSS), r = Ps(o) + `
`;
    } else
      r += `${t.themeCSS}
`;
  return r + Ps(i);
}, _1 = (t, e) => Ln(
  Sy(`${t}{${e}}`),
  wy([
    function(r, n, o, s) {
      if (r.type === "rule" && Array.isArray(r.props)) {
        if (r.parent && r.parent.type === Bn)
          return;
        r.props = r.props.map((a) => a.startsWith(t) ? a : `${t} ${a}`);
      } else
        r.type.startsWith("@") && ([
          ...[
            uy,
            dy,
            Bl,
            "@scope",
            "@container",
            "@starting-style"
          ],
          Bn
          // needed for Mermaid's animation feature
        ].includes(r.type) || (A.warn(`Removing unsupported at-rule ${r.type} from CSS`), r.type = ro));
    },
    vy
  ])
), C1 = (t, e, i, r) => {
  const n = y1(t, i), o = K_(e, n, t.themeVariables);
  return _1(r, o);
}, x1 = (t = "", e, i) => {
  let r = t;
  return !i && !e && (r = r.replace(
    /marker-end="url\([\d+./:=?A-Za-z-]*?#/g,
    'marker-end="url(#'
  )), r = cy(r), r = r.replace(/<br>/g, "<br/>"), r;
}, b1 = (t = "", e) => {
  var n, o;
  const i = (o = (n = e == null ? void 0 : e.viewBox) == null ? void 0 : n.baseVal) != null && o.height ? e.viewBox.baseVal.height + "px" : h1, r = btoa('<body style="' + u1 + '">' + t + "</body>");
  return `<iframe style="width:${l1};height:${i};${c1}" src="data:text/html;base64,${r}" sandbox="${f1}">
  ${d1}
</iframe>`;
}, ea = (t, e, i, r, n) => {
  const o = t.append("div");
  o.attr("id", i), r && o.attr("style", r);
  const s = o.append("svg").attr("id", e).attr("width", "100%").attr("xmlns", o1);
  return n && s.attr("xmlns:xlink", n), s.append("g"), t;
};
function ia(t, e) {
  return t.append("iframe").attr("id", e).attr("style", "width: 100%; height: 100%;").attr("sandbox", "");
}
const T1 = (t, e, i, r) => {
  var n, o, s;
  (n = t.getElementById(e)) == null || n.remove(), (o = t.getElementById(i)) == null || o.remove(), (s = t.getElementById(r)) == null || s.remove();
}, S1 = async function(t, e, i) {
  var Rt, L, b, C, v, x;
  fo();
  const r = Th(e);
  e = r.code;
  const n = Ut();
  A.debug(n), e.length > ((n == null ? void 0 : n.maxTextSize) ?? e1) && (e = i1);
  const o = `#${t}`, s = "i" + t, a = "#" + s, l = "d" + t, h = "#" + l;
  let u = At(document.body);
  const d = n.securityLevel === r1, c = n.securityLevel === n1, p = n.fontFamily;
  if (i !== void 0) {
    if (i && (i.innerHTML = ""), d) {
      const F = ia(At(i), s);
      u = At(F.nodes()[0].contentDocument.body), u.node().style.margin = "0";
    } else
      u = At(i);
    ea(u, t, l, `font-family: ${p}`, s1);
  } else {
    if (T1(document, t, l, s), d) {
      const F = ia(At(document.body), s);
      u = At(F.nodes()[0].contentDocument.body), u.node().style.margin = "0";
    } else
      u = At("body");
    ea(u, t, l);
  }
  let y, S;
  try {
    y = await yo(e, { title: r.title });
  } catch (F) {
    y = new ah("error"), S = F;
  }
  const M = u.select(h).node(), q = y.type, T = M.firstChild, U = T.firstChild, W = (L = (Rt = y.renderer).getClasses) == null ? void 0 : L.call(Rt, e, y), G = C1(n, q, W, o), V = document.createElement("style");
  V.innerHTML = G, T.insertBefore(V, U);
  try {
    await y.renderer.draw(e, t, Ns, y);
  } catch (F) {
    throw dC.draw(e, t, Ns), F;
  }
  const H = u.select(`${h} svg`), ue = (C = (b = y.db).getAccTitle) == null ? void 0 : C.call(b), re = (x = (v = y.db).getAccDescription) == null ? void 0 : x.call(v);
  v1(q, H, ue, re), u.select(`[id="${t}"]`).selectAll("foreignobject > *").attr("xmlns", a1);
  let j = u.select(h).node().innerHTML;
  if (A.debug("config.arrowMarkerAbsolute", n.arrowMarkerAbsolute), j = x1(j, d, Xa(n.arrowMarkerAbsolute)), d) {
    const F = u.select(h + " svg").node();
    j = b1(j, F);
  } else
    c || (j = Re.sanitize(j, {
      ADD_TAGS: p1,
      ADD_ATTR: g1,
      HTML_INTEGRATION_POINTS: { foreignobject: !0 }
    }));
  if (NC(), S)
    throw S;
  const bt = At(d ? a : h).node();
  return bt && "remove" in bt && bt.remove(), {
    svg: j,
    bindFunctions: y.db.bindFunctions
  };
};
function k1(t = {}) {
  var i;
  t != null && t.fontFamily && !((i = t.themeVariables) != null && i.fontFamily) && (t.themeVariables || (t.themeVariables = {}), t.themeVariables.fontFamily = t.fontFamily), Ay(t), t != null && t.theme && t.theme in te ? t.themeVariables = te[t.theme].getThemeVariables(
    t.themeVariables
  ) : t && (t.themeVariables = te.default.getThemeVariables(t.themeVariables));
  const e = typeof t == "object" ? By(t) : Ml();
  Dn(e.logLevel), fo();
}
const yo = (t, e = {}) => {
  const { code: i } = bh(t);
  return DC(i, e);
};
function v1(t, e, i, r) {
  ox(e, t), sx(e, i, r, e.attr("id"));
}
const be = Object.freeze({
  render: S1,
  parse: m1,
  getDiagramFromText: yo,
  initialize: k1,
  getConfig: Ut,
  setConfig: Il,
  getSiteConfig: Ml,
  updateSiteConfig: Fy,
  reset: () => {
    fr();
  },
  globalReset: () => {
    fr(We);
  },
  defaultConfig: We
});
Dn(Ut().logLevel);
fr(Ut());
const w1 = async () => {
  A.debug("Loading registered diagrams");
  const e = (await Promise.allSettled(
    Object.entries(qe).map(async ([i, { detector: r, loader: n }]) => {
      if (n)
        try {
          uo(i);
        } catch {
          try {
            const { diagram: s, id: a } = await n();
            pr(a, s, r);
          } catch (s) {
            throw A.error(`Failed to load external diagram with key ${i}. Removing from detectors.`), delete qe[i], s;
          }
        }
    })
  )).filter((i) => i.status === "rejected");
  if (e.length > 0) {
    A.error(`Failed to load ${e.length} external diagrams`);
    for (const i of e)
      A.error(i);
    throw new Error(`Failed to load ${e.length} external diagrams`);
  }
}, B1 = (t, e, i) => {
  A.warn(t), Sl(t) ? (i && i(t.str, t.hash), e.push({ ...t, message: t.str, error: t })) : (i && i(t), t instanceof Error && e.push({
    str: t.message,
    message: t.message,
    hash: t.name,
    error: t
  }));
}, Sh = async function(t = {
  querySelector: ".mermaid"
}) {
  try {
    await A1(t);
  } catch (e) {
    if (Sl(e) && A.error(e.str), Ft.parseError && Ft.parseError(e), !t.suppressErrors)
      throw A.error("Use the suppressErrors option to suppress these errors"), e;
  }
}, A1 = async function({ postRenderCallback: t, querySelector: e, nodes: i } = {
  querySelector: ".mermaid"
}) {
  const r = be.getConfig();
  A.debug(`${t ? "" : "No "}Callback function found`);
  let n;
  if (i)
    n = i;
  else if (e)
    n = document.querySelectorAll(e);
  else
    throw new Error("Nodes and querySelector are both undefined");
  A.debug(`Found ${n.length} diagrams`), (r == null ? void 0 : r.startOnLoad) !== void 0 && (A.debug("Start On Load: " + (r == null ? void 0 : r.startOnLoad)), be.updateSiteConfig({ startOnLoad: r == null ? void 0 : r.startOnLoad }));
  const o = new ui.InitIDGenerator(r.deterministicIds, r.deterministicIDSeed);
  let s;
  const a = [];
  for (const l of Array.from(n)) {
    A.info("Rendering diagram: " + l.id);
    /*! Check if previously processed */
    if (l.getAttribute("data-processed"))
      continue;
    l.setAttribute("data-processed", "true");
    const h = `mermaid-${o.next()}`;
    s = l.innerHTML, s = $h(ui.entityDecode(s)).trim().replace(/<br\s*\/?>/gi, "<br/>");
    const u = ui.detectInit(s);
    u && A.debug("Detected early reinit: ", u);
    try {
      const { svg: d, bindFunctions: c } = await Bh(h, s, l);
      l.innerHTML = d, t && await t(h), c && c(l);
    } catch (d) {
      B1(d, a, Ft.parseError);
    }
  }
  if (a.length > 0)
    throw a[0];
}, kh = function(t) {
  be.initialize(t);
}, F1 = async function(t, e, i) {
  A.warn("mermaid.init is deprecated. Please use run instead."), t && kh(t);
  const r = { postRenderCallback: i, querySelector: ".mermaid" };
  typeof e == "string" ? r.querySelector = e : e && (e instanceof HTMLElement ? r.nodes = [e] : r.nodes = e), await Sh(r);
}, L1 = async (t, {
  lazyLoad: e = !0
} = {}) => {
  ol(...t), e === !1 && await w1();
}, vh = function() {
  if (Ft.startOnLoad) {
    const { startOnLoad: t } = be.getConfig();
    t && Ft.run().catch((e) => A.error("Mermaid failed to initialize", e));
  }
};
if (typeof document < "u") {
  /*!
   * Wait for document loaded before starting the execution
   */
  window.addEventListener("load", vh, !1);
}
const E1 = function(t) {
  Ft.parseError = t;
}, _r = [];
let fn = !1;
const wh = async () => {
  if (!fn) {
    for (fn = !0; _r.length > 0; ) {
      const t = _r.shift();
      if (t)
        try {
          await t();
        } catch (e) {
          A.error("Error executing queue", e);
        }
    }
    fn = !1;
  }
}, O1 = async (t, e) => new Promise((i, r) => {
  const n = () => new Promise((o, s) => {
    be.parse(t, e).then(
      (a) => {
        o(a), i(a);
      },
      (a) => {
        var l;
        A.error("Error parsing", a), (l = Ft.parseError) == null || l.call(Ft, a), s(a), r(a);
      }
    );
  });
  _r.push(n), wh().catch(r);
}), Bh = (t, e, i) => new Promise((r, n) => {
  const o = () => new Promise((s, a) => {
    be.render(t, e, i).then(
      (l) => {
        s(l), r(l);
      },
      (l) => {
        var h;
        A.error("Error parsing", l), (h = Ft.parseError) == null || h.call(Ft, l), a(l), n(l);
      }
    );
  });
  _r.push(o), wh().catch(n);
}), Ft = {
  startOnLoad: !0,
  mermaidAPI: be,
  parse: O1,
  render: Bh,
  init: F1,
  run: Sh,
  registerExternalDiagrams: L1,
  initialize: kh,
  parseError: void 0,
  contentLoaded: vh,
  setParseErrorHandler: E1,
  detectType: Br
};
export {
  qn as $,
  ui as A,
  li as B,
  iC as C,
  rC as D,
  Z_ as E,
  nd as F,
  j1 as G,
  ty as H,
  lC as I,
  Pn as J,
  _a as K,
  Si as L,
  Lu as M,
  ba as N,
  M1 as O,
  Dh as P,
  Nh as Q,
  Ct as R,
  Tt as S,
  Ph as T,
  uC as U,
  z1 as V,
  Wp as W,
  kl as X,
  io as Y,
  Dp as Z,
  Ut as _,
  eC as a,
  Ye as a$,
  se as a0,
  gi as a1,
  Yo as a2,
  Nu as a3,
  _s as a4,
  R0 as a5,
  U1 as a6,
  J0 as a7,
  Te as a8,
  O0 as a9,
  Jo as aA,
  q1 as aB,
  R1 as aC,
  I1 as aD,
  $1 as aE,
  H1 as aF,
  W1 as aG,
  N1 as aH,
  I as aI,
  jt as aJ,
  Bi as aK,
  Ue as aL,
  ar as aM,
  g0 as aN,
  WC as aO,
  wi as aP,
  hr as aQ,
  h0 as aR,
  ul as aS,
  sm as aT,
  am as aU,
  QC as aV,
  Ls as aW,
  lm as aX,
  Jn as aY,
  nm as aZ,
  dm as a_,
  S0 as aa,
  Gt as ab,
  M0 as ac,
  I0 as ad,
  _l as ae,
  Ar as af,
  Or as ag,
  cr as ah,
  im as ai,
  Zn as aj,
  xl as ak,
  yl as al,
  l0 as am,
  i0 as an,
  k0 as ao,
  N0 as ap,
  ki as aq,
  Z1 as ar,
  nC as as,
  vi as at,
  w as au,
  O as av,
  jn as aw,
  D1 as ax,
  P1 as ay,
  Qo as az,
  tC as b,
  ce as b0,
  vs as b1,
  Qn as b2,
  dl as b3,
  Mn as b4,
  xm as b5,
  ln as b6,
  cy as b7,
  $h as b8,
  Ft as b9,
  co as c,
  Pe as d,
  na as e,
  Xn as f,
  Q_ as g,
  ht as h,
  ur as i,
  At as j,
  th as k,
  A as l,
  oy as m,
  ed as n,
  Z0 as o,
  Xa as p,
  j0 as q,
  np as r,
  J_ as s,
  G_ as t,
  K1 as u,
  ya as v,
  ry as w,
  Gh as x,
  Lm as y,
  Kn as z
};
