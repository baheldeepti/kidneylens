{#
  CMS Yes/No text -> boolean. 'Yes' -> true, 'No' -> false (case/space-insensitive).
  Anything else (blank, missing, unexpected) -> NULL, which means UNKNOWN.
  Never treat NULL as false downstream.
#}
{% macro normalize_yes_no(column) -%}
    case lower(trim({{ column }}))
        when 'yes' then true
        when 'no' then false
    end
{%- endmacro %}
