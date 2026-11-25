# Loaded into Metanorma with RUBYOPT. Skips the schema validation, as if documents set :novalid:, which saves a few
# seconds per build; previews don't show its findings. Caches bibliographic references next to the document, as if
# documents set :local-cache:, which the server keeps between builds; its HOME doesn't last. Documents can override it.
require "asciidoctor"
require "asciidoctor/extensions"

Asciidoctor::Extensions.register do
  preprocessor do
    process do |document, reader|
      document.set_attribute("novalid", "")
      document.set_attribute("local-cache", "")
      reader
    end
  end
end
