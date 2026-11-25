# Loaded into Metanorma with RUBYOPT. Gives the first block of every included AsciiDoc file an id, in memory: the
# anchor the file has, or a new one. Writes which id belongs to which file to $SOURCE_MAP on exit.
# Metanorma resolves includes before it parses, so Asciidoctor's sourcemap can't tell the files apart.
require "asciidoctor"
require "digest"
require "json"

module SourceMap
  ANCHOR = /\A\s*(?:\[\[([^\],\s]+)[^\]]*\]\]|\[#([^\],.%\s]+)[^\]]*\])/
  # Comments, attribute entries and blank lines before the first block
  SKIP = %r{\A\s*(?://|:[\w-]+:|\z)}
  ATTRIBUTES = /\A\s*\[.*\]\s*\z/
  MAP = {}

  def self.mark(lines, file)
    index = lines.index { |line| !SKIP.match?(line) }
    # The anchor of a file that starts with an include would end up on the included file's block
    return lines if index.nil? || lines[index].start_with?("include::")

    header = lines[index..].take_while { |line| SKIP.match?(line) || ATTRIBUTES.match?(line) }
    if (match = header.lazy.map { |line| ANCHOR.match(line) }.find(&:itself))
      MAP[match[1] || match[2]] ||= file
      return lines
    end
    id = "mn-source-#{Digest::SHA1.hexdigest(file)[0, 12]}"
    MAP[id] ||= file
    lines.dup.insert(index, "[[#{id}]]")
  end

  module PushInclude
    def push_include(data, file = nil, path = nil, lineno = 1, attributes = {})
      if ::String === file && file.end_with?(".adoc")
        begin
          data = SourceMap.mark(::String === data ? data.lines.map(&:chomp) : data, file)
        rescue StandardError => e
          warn "source map: #{e.message}"
        end
      end
      super
    end
  end
end

if ENV["SOURCE_MAP"]
  Asciidoctor::PreprocessorReader.prepend(SourceMap::PushInclude)
  at_exit { File.write(ENV["SOURCE_MAP"], JSON.generate(SourceMap::MAP)) }
end
