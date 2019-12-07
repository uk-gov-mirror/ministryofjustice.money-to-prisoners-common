'use strict';

const webpack = require('webpack');

module.exports = {
  mode: 'none',
  entry: './{{ app.javascript_source_path }}/main.js',
  output: {
    path: __dirname + '/{{ app.javascript_build_path }}',
    filename: 'app.bundle.js'
  },
  resolve: {
    modules: [
      __dirname + '/node_modules',
      {% for path in app.javascript_include_paths %}
        '{{ path }}'{% if not forloop.last %},{% endif %}
      {% endfor %}
    ]
  },
  plugins: [
    new webpack.ProvidePlugin({
      $: 'jquery',
      jQuery: 'jquery'
    })
  ]
};
