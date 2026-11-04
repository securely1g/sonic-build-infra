# Debug symbols are data, not executable programs. Keep directories, links and
# all other metadata exactly as the upstream mtree pipeline produced them.
/(^|[[:space:]])type=file([[:space:]]|$)/ && !/(^|[[:space:]])link=/ {
    sub(/(^|[[:space:]])mode=[0-7]+([[:space:]]|$)/, " mode=" debug_file_mode " ")
}
